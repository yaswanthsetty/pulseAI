"""LLM judge for the eval harness (docs/plans/phase2_eval.md §3).

Rules enforced here:

* local Ollama only, no paid API; the judge is never ``qwen2.5:3b`` (the model
  under test) — that is asserted, not assumed.
* the rubric is the frozen prompt at ``eval/prompts/judge.md``; its sha256 is
  the judge version recorded in every results file.
* start with ``qwen2.5:14b``; if a single judgment exceeds ~20 s on this
  machine, fall back to ``qwen2.5:7b`` (env ``EVAL_JUDGE_FALLBACK``) and say
  so in the log. Both the attempted and the effective model are recorded.
* responses must be strict JSON ``{"score": 0|1|2, "reason": "..."}``;
  one retry on parse failure, then the item is counted as a judge error.

The transport is injectable so the CI mocked subset can run without Ollama.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUBRIC_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
MODEL_UNDER_TEST = "qwen2.5:3b"
DEFAULT_JUDGE_MODEL = "qwen2.5:14b"
DEFAULT_FALLBACK_MODEL = "qwen2.5:7b"
SLOW_JUDGMENT_SECONDS = 20.0

# transport(model, system_prompt, user_prompt) -> raw text; injectable for CI.
Transport = Callable[[str, str, str], str]


def judge_version() -> str:
    """sha256 of the frozen rubric — a prompt change is a new judge version."""
    return hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()[:12]


def parse_judgment(text: str) -> dict[str, Any]:
    """Parse a strict-JSON judgment; tolerate one fenced/extra-brace wrapping."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object in judge output: {text[:200]!r}") from None
        data = json.loads(match.group(0))
    score = data.get("score")
    if score not in (0, 1, 2):
        raise ValueError(f"judge score out of range: {score!r}")
    return {"score": int(score), "reason": str(data.get("reason", ""))}


def _ollama_transport(model: str, system_prompt: str, user_prompt: str) -> str:
    import httpx
    from backend.core.config import settings

    response = httpx.post(
        f"{settings.ollama_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_ctx": 8192},
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "")


@dataclass
class Judge:
    """One judge instance: model + rubric + call accounting."""

    model: str = field(
        default_factory=lambda: os.environ.get("EVAL_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
    )
    fallback_model: str = field(
        default_factory=lambda: os.environ.get("EVAL_JUDGE_FALLBACK", DEFAULT_FALLBACK_MODEL)
    )
    transport: Transport = _ollama_transport
    slow_seconds: float = SLOW_JUDGMENT_SECONDS
    calls: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.model == MODEL_UNDER_TEST or self.fallback_model == MODEL_UNDER_TEST:
            raise ValueError(f"the model under test ({MODEL_UNDER_TEST}) can never be the judge")
        self._system_prompt = RUBRIC_PATH.read_text(encoding="utf-8")

    @property
    def effective_model(self) -> str:
        return self.fallback_model if self.fallback_used else self.model

    @property
    def version(self) -> str:
        return f"{self.effective_model}@rubric:{judge_version()}"

    def _fall_back(self, reason: str) -> None:
        print(f"judge: {reason}; falling back to {self.fallback_model}")
        self.fallback_used = True
        self.fallback_reason = reason

    def judge(self, task: str, items: dict[str, str]) -> dict[str, Any]:
        """Score one item; returns {score, reason, model, latency_ms} or an error."""
        user_prompt = f"Task: {task}\n\n" + "\n".join(f"{k}: {v}" for k, v in items.items())
        for attempt in (1, 2):
            model = self.effective_model
            started = time.monotonic()
            try:
                raw = self.transport(model, self._system_prompt, user_prompt)
            except Exception as exc:  # noqa: BLE001 - judge failures are recorded, not raised
                self.errors.append(f"{task}: attempt {attempt}: {exc}")
                # A preferred model that times out is exactly the case the
                # fallback exists for: without this, every judgment would be
                # counted as a judge error and the metrics would be built on
                # nulls instead of on the fallback model's answers.
                if not self.fallback_used:
                    self._fall_back(f"{model} failed ({exc})")
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            self.calls += 1
            self.latencies_ms.append(latency_ms)
            if latency_ms >= self.slow_seconds * 1000 and not self.fallback_used:
                self._fall_back(f"{model} took {latency_ms / 1000:.1f}s > {self.slow_seconds:.0f}s")
                continue  # re-judge this item with the fallback model
            try:
                parsed = parse_judgment(raw)
            except ValueError as exc:
                self.errors.append(f"{task}: attempt {attempt}: {exc}")
                continue
            return {**parsed, "model": model, "latency_ms": latency_ms}
        return {"score": None, "reason": "judge error", "model": model, "latency_ms": None}

    def agreement(self, human_labels: list[tuple[dict[str, Any], int]]) -> dict[str, float]:
        """Cohen's kappa between judge scores and human labels (raw + kappa)."""
        pairs = [(j["score"], human) for j, human in human_labels if j["score"] is not None]
        if not pairs:
            return {"n": 0, "raw_agreement": 0.0, "kappa": 0.0}
        n = len(pairs)
        agree = sum(1 for j, h in pairs if j == h)
        marginals = [
            sum(1 for j, _ in pairs if j == s) / n * sum(1 for _, h in pairs if h == s) / n
            for s in (0, 1, 2)
        ]
        p_e = sum(marginals)
        kappa = 1.0 if p_e == 1.0 else (agree / n - p_e) / (1 - p_e)
        return {"n": n, "raw_agreement": agree / n, "kappa": round(kappa, 4)}


def build_judge() -> Judge:
    return Judge()
