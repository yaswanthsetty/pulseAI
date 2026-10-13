"""RAG suite: fast and deep chat paths scored separately (plan §2.2).

Phases:

* ``run``   — one chat per query per path against the frozen snapshot, using
  the exact router wiring (fast: ``search(message, limit=5)`` then
  ``chat_stream``; deep: ``chat_stream_deep`` with an injected ``search_fn``).
  Records the answer, evidence, invalid citations, planner sub-questions,
  retrieved chunk sets and latency. ``--mock`` swaps the paths for canned
  deterministic fixtures (CI arithmetic test, no Ollama/Qdrant).
* ``score`` — citation precision/coverage, required-fact recall,
  invalid-citation rate, refusal-when-evidence-exists (deterministic regex
  first, LLM judge only for borderline cases — counted), sub-question
  relevance (LLM judge), latency p50/p95 (cold vs warm). Unjudged chunks
  count as 0 via eval/labels.jsonl. Writes ``eval/results/<date>_<sha>_rag.*``.

Usage:
    uv run python -m eval.rag_suite run [--limit N] [--mock]
    uv run python -m eval.rag_suite score [--mock]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
WORK_DIR = EVAL_DIR / "work"
RESULTS_DIR = EVAL_DIR / "results"
QUERIES_PATH = EVAL_DIR / "queries.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"

CITATION_RE = re.compile(r"\[#(\d+)\]")
FAST_LIMIT = 5  # router wiring: fast path retrieves top-5
EVAL_USER_EMAIL = "eval@pulseai.local"  # fixed eval user: empty history, no cross-talk


def _git_sha() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _latest_snapshot_id() -> str:
    snapshots = sorted((EVAL_DIR / "snapshots").glob("*.json"))
    if not snapshots:
        raise SystemExit("no snapshots in eval/snapshots — run python -m eval.make_snapshot")
    return snapshots[-1].stem


def load_queries(snapshot_id: str) -> list[dict[str, Any]]:
    queries = [
        json.loads(line) for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines() if line
    ]
    mismatched = [q["id"] for q in queries if q.get("snapshot") != snapshot_id]
    if mismatched:
        raise SystemExit(f"queries not on snapshot {snapshot_id}: {mismatched}")
    return queries


def load_labels() -> dict[str, dict[str, int]]:
    """query_id -> {chunk_id: grade} from eval/labels.jsonl (pool grades)."""
    labels: dict[str, dict[str, int]] = {}
    if not LABELS_PATH.exists():
        raise SystemExit("eval/labels.jsonl missing — run: python -m eval.retrieval_suite grade")
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels.setdefault(row["query_id"], {})[row["chunk_id"]] = row["grade"]
    return labels


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return ordered[index]


async def _drain(stream) -> list[str]:
    """Pull an async SSE generator to completion (sync harness)."""
    chunks: list[str] = []
    async for item in stream:
        chunks.append(item)
    return chunks


def _parse_sse(chunks: list[str]) -> dict[str, Any]:
    """Fold an SSE stream (list of yielded strings) into one record."""
    events: list[dict[str, Any]] = []
    for raw in chunks:
        line = raw[5:].strip() if raw.startswith("data: ") else raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    answer = ""
    evidence: list[dict[str, Any]] = []
    invalid_citations: list[int] = []
    agreement: float | None = None
    error: str | None = None
    sub_questions: list[str] = []
    first_token_ms: int | None = None
    for event in events:
        etype = event.get("type")
        if etype == "token":
            answer += event.get("token", "")
            if first_token_ms is None:
                first_token_ms = -1  # caller fills wall clock
        elif etype == "thinking" and event.get("stage") == "planner":
            sub_questions = list(event.get("sub_questions") or [])
        elif etype == "evidence":
            answer = event.get("message", answer)
            evidence = event.get("evidence") or []
            invalid_citations = list(event.get("invalid_citations") or [])
            agreement = event.get("agreement")
        elif etype == "error" or event.get("error"):
            error = event.get("error") or event.get("message")
    return {
        "answer": answer.strip(),
        "evidence": [
            {
                "citation_id": e.get("citation_id"),
                "chunk_id": str(e["chunk_id"]) if e.get("chunk_id") else None,
                "article_id": str(e.get("article_id")),
                "title": e.get("title"),
            }
            for e in evidence
        ],
        "invalid_citations": invalid_citations,
        "agreement": agreement,
        "sub_questions": sub_questions,
        "had_first_token": first_token_ms is not None,
        "error": error,
    }


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def run(snapshot_id: str, limit: int | None = None, mock: bool = False) -> Path:
    queries = load_queries(snapshot_id)
    if limit:
        queries = queries[:limit]

    if mock:
        from eval.mock import canned_chat_deep, canned_chat_fast

        makers = {"fast": canned_chat_fast, "deep": canned_chat_deep}
        user_id = "00000000-0000-0000-0000-000000000000"
    else:
        from backend.core.database import SessionLocal
        from backend.db.models import User
        from backend.modules.agents import service as agent_service
        from backend.modules.agents.schemas import ChatRequest
        from backend.modules.auth import service as auth_service
        from backend.modules.retrieval import service as retrieval
        from sqlalchemy import select

        makers = None
        eval_db = SessionLocal()
        try:
            user = eval_db.scalar(select(User).where(User.email == EVAL_USER_EMAIL))
            if user is None:
                user = auth_service.register_user(
                    eval_db,
                    email=EVAL_USER_EMAIL,
                    password="eval-harness-not-a-login",
                    display_name="eval harness",
                )
            user_id = str(user.id)
        finally:
            eval_db.close()

    records: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        for path in ("fast", "deep"):
            record: dict[str, Any] = {
                "query_id": query["id"],
                "type": query["type"],
                "path": path,
                "message": query["query"],
            }
            started = time.monotonic()
            if mock:
                parsed = makers[path](query)  # type: ignore[misc]
                parsed["latency_ms"] = int((time.monotonic() - started) * 1000)
                parsed["retrieved_chunk_ids"] = parsed.get("retrieved_chunk_ids", [])
            else:
                db = SessionLocal()
                try:
                    if path == "fast":
                        results = retrieval.search(query=query["query"], limit=FAST_LIMIT)
                        retrieved = [str(r.chunk_id) for r in results if r.chunk_id]
                        for r in results:
                            retrieved.extend(str(e.chunk_id) for e in r.extra_chunks)
                        stream = agent_service.chat_stream(
                            db,
                            uuid.UUID(user_id),
                            ChatRequest(message=query["query"]),
                            context=results,
                        )
                    else:
                        retrieved = []

                        def _search(  # noqa: F811 - injected as the deep path's search_fn
                            query: str, limit: int, _acc: list[str] = retrieved
                        ):
                            hits = retrieval.search(query=query, limit=limit)
                            _acc.extend(str(h.chunk_id) for h in hits if h.chunk_id)
                            for h in hits:
                                _acc.extend(str(e.chunk_id) for e in h.extra_chunks)
                            return hits

                        stream = agent_service.chat_stream_deep(
                            db,
                            uuid.UUID(user_id),
                            ChatRequest(message=query["query"]),
                            search_fn=_search,
                        )
                    chunks = asyncio.run(_drain(stream))
                    parsed = _parse_sse(chunks)
                    parsed["latency_ms"] = int((time.monotonic() - started) * 1000)
                    parsed["retrieved_chunk_ids"] = retrieved
                finally:
                    db.close()
            record.update(parsed)
            records.append(record)
            print(
                f"  [{index}/{len(queries)}] {query['id']} {path}: "
                f"{record['latency_ms']}ms, {len(record['answer'])} chars, "
                f"{len(record['evidence'])} evidence, err={record['error']}"
            )

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_mock" if mock else ""
    raw_path = WORK_DIR / f"{snapshot_id}_rag_raw{suffix}.json"
    raw_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "git_sha": _git_sha(),
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "mock": mock,
                "user_id": str(user_id),
                "records": records,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"raw records : {raw_path}")
    return raw_path


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _metric_set(
    record: dict[str, Any],
    grades: dict[str, int],
    facts: list[str],
    judge: Any | None,
) -> dict[str, Any]:
    """All deterministic RAG metrics for one chat record."""
    from eval import metrics as M
    from eval import refusal as R

    answer = record["answer"]
    evidence = record["evidence"]
    cited_ids = [int(c) for c in CITATION_RE.findall(answer)]
    evidence_by_citation = {
        e["citation_id"]: e for e in evidence if e.get("citation_id") is not None
    }

    # Citation precision: cited ids resolving to a grade >= 1 chunk.
    resolved = [
        evidence_by_citation[cid].get("chunk_id")
        for cid in cited_ids
        if cid in evidence_by_citation
    ]
    graded = [grades.get(chunk_id, 0) for chunk_id in resolved if chunk_id]
    citation_precision = sum(1 for g in graded if g >= 1) / len(graded) if graded else None

    # Citation coverage: grade-2 retrieved chunks actually cited.
    retrieved = record.get("retrieved_chunk_ids") or []
    grade2_retrieved = {cid for cid in retrieved if grades.get(cid, 0) == 2}
    cited_chunks = {chunk_id for chunk_id in resolved if chunk_id}
    citation_coverage = (
        len(grade2_retrieved & cited_chunks) / len(grade2_retrieved) if grade2_retrieved else None
    )

    # Required-fact recall.
    fact_recall = M.macro_mean([1.0 if fact.lower() in answer.lower() else 0.0 for fact in facts])

    # Invalid-citation rate (pipeline strips them; count over all attempts).
    attempts = len(cited_ids) + len(record.get("invalid_citations") or [])
    invalid_rate = len(record.get("invalid_citations") or []) / attempts if attempts else None

    # Refusal-when-evidence-exists.
    verdict = R.classify(answer, required_facts=facts, grade2_retrieved=bool(grade2_retrieved))
    judge_routed = False
    refusal = verdict == "refusal"
    judge_score: int | None = None
    judge_item: dict[str, Any] | None = None
    if verdict == "borderline" and judge is not None:
        judge_routed = True
        items = {"QUESTION": record["message"], "ANSWER": answer or "(empty)"}
        result = judge.judge("implicit_refusal", items)
        judge_score = result["score"]
        refusal = bool(judge_score is not None and judge_score >= 1)
        judge_item = {"items": items, "result": result}

    return {
        "judge_item": judge_item,
        "citation_precision": citation_precision,
        "citation_coverage": citation_coverage,
        "required_fact_recall": fact_recall,
        "invalid_citation_rate": invalid_rate,
        "refusal": refusal,
        "refusal_verdict": verdict,
        "judge_routed": judge_routed,
        "judge_score": judge_score,
        "n_cited": len(cited_ids),
        "n_grade2_retrieved": len(grade2_retrieved),
    }


def score(snapshot_id: str, mock: bool = False) -> tuple[Path, Path]:
    from eval import metrics as M

    suffix = "_mock" if mock else ""
    raw = json.loads((WORK_DIR / f"{snapshot_id}_rag_raw{suffix}.json").read_text(encoding="utf-8"))
    queries = {q["id"]: q for q in load_queries(snapshot_id)}
    labels = load_labels()

    judge = None
    judge_info: dict[str, Any]
    if mock:
        from eval.mock import canned_judge

        judge = canned_judge()
        judge_info = {"version": judge.version, "model": judge.effective_model, "mock": True}
    else:
        try:
            from eval.judge import build_judge

            judge = build_judge()
            judge_info = {
                "version": judge.version,
                "model": judge.model,
                "fallback_model": judge.fallback_model,
                "mock": False,
            }
        except Exception as exc:  # noqa: BLE001 - judge optional at runtime
            judge = None
            judge_info = {"version": None, "model": None, "error": str(exc)}

    per_path: dict[str, list[dict[str, Any]]] = {"fast": [], "deep": []}
    subq_scores: list[float] = []
    subq_judged = 0
    subq_errors = 0
    # Every LLM judgment, kept for the audit trail and the human agreement
    # sample (plan §3): the results file records what the judge was asked.
    judge_items: list[dict[str, Any]] = []
    latencies: dict[str, list[float]] = {"fast": [], "deep": []}
    cold: dict[str, float | None] = {"fast": None, "deep": None}
    refusal_counts = {"fast": 0, "deep": 0}
    judge_routed_counts = {"fast": 0, "deep": 0}

    cache = _JudgeCache(WORK_DIR / f"{snapshot_id}_rag_judge{suffix}.json")
    if judge is not None:
        judge.judge = cache.wrap(judge.judge)  # type: ignore[method-assign]

    for record in raw["records"]:
        query = queries[record["query_id"]]
        grades = labels.get(record["query_id"], {})
        facts = list(query["required_facts"])
        metrics_row = _metric_set(record, grades, facts, judge)
        refusal_item = metrics_row.pop("judge_item")
        if refusal_item is not None:
            judge_items.append(
                _judged_item(
                    record,
                    "implicit_refusal",
                    refusal_item["items"],
                    refusal_item["result"],
                    record["message"],
                )
            )

        if record["path"] == "deep" and record.get("sub_questions") and judge is not None:
            for sub_q in record["sub_questions"]:
                items = {"USER QUERY": record["message"], "SUB-QUESTION": sub_q}
                result = judge.judge("sub_question_relevance", items)
                judge_items.append(
                    _judged_item(record, "sub_question_relevance", items, result, sub_q)
                )
                if result["score"] is None:
                    subq_errors += 1
                else:
                    subq_judged += 1
                    subq_scores.append(result["score"])
                if subq_judged % 20 == 0:
                    print(
                        f"  judged {subq_judged} sub-questions "
                        f"({subq_errors} errors, mean "
                        f"{sum(subq_scores) / len(subq_scores):.3f})",
                        flush=True,
                    )
        per_path[record["path"]].append(
            {"query_id": record["query_id"], "type": record["type"], **metrics_row}
        )
        latencies[record["path"]].append(record["latency_ms"])
        if cold[record["path"]] is None:
            cold[record["path"]] = record["latency_ms"]
        refusal_counts[record["path"]] += 1 if metrics_row["refusal"] else 0
        judge_routed_counts[record["path"]] += 1 if metrics_row["judge_routed"] else 0

    aggregates: dict[str, Any] = {}
    for path, rows in per_path.items():
        numeric_keys = [
            "citation_precision",
            "citation_coverage",
            "required_fact_recall",
            "invalid_citation_rate",
        ]
        agg = {key: M.macro_mean([row[key] for row in rows]) for key in numeric_keys}
        for key in numeric_keys:
            agg[f"{key}_n"] = len([row[key] for row in rows if row[key] is not None])
        n = len(rows)
        agg["n_queries"] = n
        agg["refusal_count"] = refusal_counts[path]
        agg["refusal_rate"] = refusal_counts[path] / n if n else None
        # The plan's T1-deep metric is refusal *when evidence exists*: refusing
        # on a query with no grade-2 chunk retrieved is correct behaviour, and
        # blending the two would make a healthy path look broken (and vice
        # versa). Both sides are reported; the overall rate stays for context.
        agg.update(_refusal_breakdown(rows))
        agg["judge_routed_count"] = judge_routed_counts[path]
        warm = latencies[path][1:]
        agg["latency_ms"] = {
            "cold": cold[path],
            "warm_p50": percentile(warm, 50),
            "warm_p95": percentile(warm, 95),
            "warm_n": len(warm),
        }
        by_type: dict[str, Any] = {}
        for qtype in sorted({row["type"] for row in rows}):
            subset = [row for row in rows if row["type"] == qtype]
            by_type[qtype] = {
                key: M.macro_mean([row[key] for row in subset]) for key in numeric_keys
            }
            by_type[qtype]["refusal_rate"] = (
                sum(1 for row in subset if row["refusal"]) / len(subset) if subset else None
            )
            by_type[qtype].update(_refusal_breakdown(subset))
        agg["by_type"] = by_type
        aggregates[path] = agg

    cache.save()
    if judge is not None:
        _record_judge_provenance(judge_info, judge, judge_items, cache)

    subq_relevance = M.macro_mean(subq_scores) if subq_scores else None
    results = {
        "suite": "rag",
        "snapshot_id": snapshot_id,
        "git_sha": raw["git_sha"],
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "mock": mock,
        "queries_file": "eval/queries.jsonl",
        "query_set_status": queries[sorted(queries)[0]]["status"],
        "judge": judge_info,
        "sub_question_relevance": {
            "mean": subq_relevance,
            "judged": subq_judged,
            "errors": subq_errors,
        },
        "judge_items": judge_items,
        "aggregates": aggregates,
        "per_query": per_path,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    git_sha = raw["git_sha"]
    name = f"{stamp}_{git_sha}_rag{'_mock' if mock else ''}"
    json_path = RESULTS_DIR / f"{name}.json"
    json_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    md_path = RESULTS_DIR / f"{name}.md"
    md_path.write_text(_render_md(results), encoding="utf-8")
    print(_render_md(results))
    print(f"written     : {json_path}\n              {md_path}")
    return json_path, md_path


class _JudgeCache:
    """Disk cache for judge decisions, keyed by (task, item text).

    Judging the deep path costs tens of minutes; a crash in the aggregation
    afterwards must not throw that away, and re-scoring to change a metric
    must not re-ask the model. The cache is written incrementally, so an
    interrupted run keeps every judgment it already paid for.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8"))
        self.hits = 0
        self._since_save = 0

    @staticmethod
    def key(task: str, items: dict[str, str]) -> str:
        payload = json.dumps([task, sorted(items.items())], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def wrap(self, judge) -> Any:
        def cached(task: str, items: dict[str, str]) -> dict[str, Any]:
            cache_key = self.key(task, items)
            if cache_key in self.entries:
                self.hits += 1
                return self.entries[cache_key]
            result = judge(task, items)
            self.entries[cache_key] = result
            self._since_save += 1
            if self._since_save >= 5:
                self.save()
            return result

        return cached

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries), encoding="utf-8")
        self._since_save = 0


def _refusal_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Refusal split by whether grade-2 evidence was retrieved at all."""
    with_evidence = [row for row in rows if (row.get("n_grade2_retrieved") or 0) > 0]
    without = [row for row in rows if not (row.get("n_grade2_retrieved") or 0)]
    refused_with = sum(1 for row in with_evidence if row["refusal"])
    refused_without = sum(1 for row in without if row["refusal"])
    return {
        "n_with_evidence": len(with_evidence),
        "refusal_when_evidence_exists_count": refused_with,
        "refusal_when_evidence_exists_rate": (
            refused_with / len(with_evidence) if with_evidence else None
        ),
        "n_without_evidence": len(without),
        "refusal_without_evidence_count": refused_without,
        "refusal_without_evidence_rate": (
            refused_without / len(without) if without else None
        ),
    }


def _record_judge_provenance(
    judge_info: dict[str, Any], judge: Any, judge_items: list[dict[str, Any]], cache: _JudgeCache
) -> None:
    """Record which judge model actually produced these numbers.

    The preferred model can fall back mid-run, and a cache replay makes no
    calls at all, so ``judge.effective_model`` and ``judge.calls`` cannot be
    trusted for provenance. Every judgment carries the model that produced it,
    so the judgments decide.
    """
    from eval.judge import judge_version

    judge_info["cached_judgments"] = cache.hits
    judge_info["calls"] = getattr(judge, "calls", None)
    errors = list(getattr(judge, "errors", []))
    judge_info["errors"] = len(errors)
    judge_info["error_samples"] = errors[:5]
    judge_info["fallback_reason"] = getattr(judge, "fallback_reason", None)

    models = Counter(item["model"] for item in judge_items if item.get("model"))
    judge_info["models_used"] = dict(models)
    if models:
        effective = models.most_common(1)[0][0]
        judge_info["effective_model"] = effective
        judge_info["fallback_used"] = effective != judge.model
    else:
        judge_info["effective_model"] = judge.effective_model
        judge_info["fallback_used"] = judge.fallback_used
    # The version string is the provenance a result is compared against, so it
    # must name the model that answered, not the one that was requested.
    judge_info["version"] = f"{judge_info['effective_model']}@rubric:{judge_version()}"


def _judged_item(
    record: dict[str, Any],
    task: str,
    items: dict[str, str],
    result: dict[str, Any],
    subject: str,
) -> dict[str, Any]:
    """One auditable judge record (input text in, score + reason out)."""
    return {
        "query_id": record["query_id"],
        "path": record["path"],
        "task": task,
        "subject": subject,
        "items": items,
        "score": result.get("score"),
        "reason": result.get("reason"),
        "model": result.get("model"),
        "latency_ms": result.get("latency_ms"),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _render_md(results: dict[str, Any]) -> str:
    lines = [
        f"# RAG suite — snapshot `{results['snapshot_id']}` ({results['git_sha']})",
        "",
        f"Queries: {results['aggregates']['fast']['n_queries']} x 2 paths "
        f"(status: **{results['query_set_status']}**) · judge: "
        f"`{results['judge'].get('version')}` (requested "
        f"`{results['judge'].get('model')}`"
        + (
            "; fell back"
            + (
                f" ({results['judge']['fallback_reason']})"
                if results["judge"].get("fallback_reason")
                else ""
            )
            if results["judge"].get("fallback_used")
            else ""
        )
        + f"; cached judgments {results['judge'].get('cached_judgments')})"
        f" · mock={results['mock']}.",
        "",
        "| metric | fast | deep |",
        "|---|---|---|",
    ]
    for key in (
        "citation_precision",
        "citation_coverage",
        "required_fact_recall",
        "invalid_citation_rate",
        "refusal_rate",
        "refusal_when_evidence_exists_rate",
        "refusal_without_evidence_rate",
        "judge_routed_count",
    ):
        lines.append(
            f"| {key} | {_fmt(results['aggregates']['fast'].get(key))} "
            f"| {_fmt(results['aggregates']['deep'].get(key))} |"
        )
    sq = results["sub_question_relevance"]
    lines += [
        f"| sub_question_relevance | — | {_fmt(sq['mean'])} "
        f"(n={sq['judged']}, err={sq['errors']}) |",
        "",
        "## Latency (ms)",
        "",
        "| path | cold | warm p50 | warm p95 | warm n |",
        "|---|---|---|---|---|",
    ]
    for path in ("fast", "deep"):
        lat = results["aggregates"][path]["latency_ms"]
        lines.append(
            f"| {path} | {_fmt(lat['cold'])} | {_fmt(lat['warm_p50'])} "
            f"| {_fmt(lat['warm_p95'])} | {lat['warm_n']} |"
        )
    lines += [
        "",
        "## Per-type (required_fact_recall / refusal-when-evidence-exists)",
        "",
        "| type | fast fact / refusal | deep fact / refusal |",
        "|---|---|---|",
    ]
    types = sorted(results["aggregates"]["fast"]["by_type"])
    for qtype in types:
        f = results["aggregates"]["fast"]["by_type"][qtype]
        d = results["aggregates"]["deep"]["by_type"][qtype]
        lines.append(
            f"| {qtype} | {_fmt(f['required_fact_recall'])} / "
            f"{_fmt(f['refusal_when_evidence_exists_rate'])} "
            f"| {_fmt(d['required_fact_recall'])} / "
            f"{_fmt(d['refusal_when_evidence_exists_rate'])} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["run", "score"])
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    snapshot_id = args.snapshot or _latest_snapshot_id()
    if args.phase == "run":
        run(snapshot_id, limit=args.limit, mock=args.mock)
    else:
        score(snapshot_id, mock=args.mock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
