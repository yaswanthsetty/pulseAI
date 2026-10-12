"""Deterministic fakes for the mocked CI subset (plan §5).

The purpose is to exercise the **harness arithmetic and wiring** with zero
network: no Ollama, no Qdrant, no BGE-M3. Everything here is a pure function
of its inputs, so the produced metrics are byte-for-byte reproducible — which
is what makes the CI exact-equality gate meaningful.

"CI numbers are not quality numbers."
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eval"

# Chunk universe for the fake corpus — mirrors the four real sources.
FAKE_ARTICLES: dict[str, dict[str, Any]] = {
    "art-alpha": {
        "title": "Alpha Corp ships the X1 chip",
        "source_name": "TechCrunch",
        "fact": "The X1 chip delivers 45% better efficiency.",
        "decoy": "Alpha Corp also sells laptop bags and stickers.",
    },
    "art-beta": {
        "title": "Beta Labs benchmarks the X1",
        "source_name": "BBC",
        "fact": "Benchmarks confirmed the 45% efficiency claim under load.",
        "decoy": "Beta Labs is hiring a marketing intern in Manchester.",
    },
    "art-gamma": {
        "title": "Gamma University studies chip thermals",
        "source_name": "The Verge",
        "fact": "Thermal throttling begins at 82C on open-frame rigs.",
        "decoy": "Gamma University's mascot is a river otter.",
    },
    "art-delta": {
        "title": "Delta Motors switches to in-house silicon",
        "source_name": "The Guardian Technology",
        "fact": "Delta Motors will use X1 modules in its 2027 models.",
        "decoy": "Delta Motors' cafeteria serves trout on Fridays.",
    },
    "art-noise": {
        "title": "Weekend roundup: gadgets we liked",
        "source_name": "TechCrunch",
        "fact": "Staff picked a kettle as gadget of the week.",
        "decoy": "The kettle holds 1.7 litres and costs $39.",
    },
}


def _chunk_id(article_id: str, slot: str) -> str:
    """Stable fake chunk ids shaped like the real (uuid) ones."""
    digest = hashlib.sha256(f"{article_id}:{slot}".encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def fixture_queries() -> list[dict[str, Any]]:
    """The 12 mocked queries: 3 per type, both path hints covered."""
    spec = [
        ("m001", "fact", "fast", "art-alpha", "45% better efficiency", ["art-alpha", "art-beta"]),
        ("m002", "fact", "fast", "art-gamma", "82C", ["art-gamma"]),
        ("m003", "fact", "deep", "art-delta", "2027 models", ["art-delta"]),
        ("m004", "compare", "deep", "art-alpha", "45%", ["art-alpha", "art-beta"]),
        ("m005", "compare", "fast", "art-gamma", "82C", ["art-gamma", "art-alpha"]),
        ("m006", "compare", "fast", "art-delta", "X1", ["art-delta", "art-alpha"]),
        ("m007", "multi-hop", "deep", "art-beta", "45%", ["art-alpha", "art-beta"]),
        ("m008", "multi-hop", "deep", "art-delta", "X1 modules", ["art-delta", "art-alpha"]),
        ("m009", "multi-hop", "deep", "art-gamma", "82C", ["art-gamma", "art-beta"]),
        ("m010", "time-bound", "fast", "art-delta", "2027", ["art-delta"]),
        ("m011", "time-bound", "fast", "art-noise", "$39", ["art-noise"]),
        ("m012", "time-bound", "deep", "art-alpha", "45%", ["art-alpha"]),
    ]
    queries = []
    for qid, qtype, path_hint, lead, fact, relevant in spec:
        queries.append(
            {
                "id": qid,
                "snapshot": "mock",
                "query": f"Mock query {qid}: what do the sources say ({lead})?",
                "type": qtype,
                "language": "en",
                "path_hint": path_hint,
                "relevant_article_ids": relevant,
                "relevant_chunk_ids": [_chunk_id(a, "fact") for a in relevant],
                "required_facts": [fact],
                "relevance_labels": {},
                "status": "draft_unreviewed",
                "labeler": "coding-agent",
                "drafted_at": "2026-10-11",
            }
        )
    return queries


def load_fixture_queries() -> list[dict[str, Any]]:
    """The committed CI query fixture: the frozen input the CI subset runs on.

    Reading the file (rather than calling ``fixture_queries()``) is what makes
    ``tests/fixtures/eval/`` a load-bearing part of the gate: a query-set edit
    shows up as a reviewed diff in the fixture, not as a silent code change.
    """
    path = FIXTURES_DIR / "queries.jsonl"
    if not path.exists():
        raise SystemExit(f"missing CI fixture {path} — run: uv run python -m eval.mock")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write_mock_dataset() -> None:
    """(Re)write the committed mock fixtures: queries + pool texts + labels."""
    queries = fixture_queries()
    queries_path = FIXTURES_DIR / "queries.jsonl"
    queries_path.parent.mkdir(parents=True, exist_ok=True)
    queries_path.write_text("\n".join(json.dumps(q) for q in queries) + "\n", encoding="utf-8")
    texts: dict[str, dict[str, Any]] = {}
    labels: list[dict[str, Any]] = []
    for query in queries:
        lead = query["relevant_article_ids"][0]
        texts.setdefault(
            _chunk_id(lead, "fact"),
            {
                "article_id": lead,
                "title": FAKE_ARTICLES[lead]["title"],
                "source_name": FAKE_ARTICLES[lead]["source_name"],
                "chunk_number": 0,
                "text": FAKE_ARTICLES[lead]["fact"],
            },
        )
        for article_id, article in FAKE_ARTICLES.items():
            for slot in ("fact", "decoy"):
                cid = _chunk_id(article_id, slot)
                texts.setdefault(
                    cid,
                    {
                        "article_id": article_id,
                        "title": article["title"],
                        "source_name": article["source_name"],
                        "chunk_number": 0 if slot == "fact" else 1,
                        "text": article[slot],
                    },
                )
                if cid in query["relevant_chunk_ids"]:
                    grade = 2
                elif article_id in query["relevant_article_ids"]:
                    grade = 1
                else:
                    grade = 0
                labels.append(
                    {
                        "query_id": query["id"],
                        "chunk_id": cid,
                        "article_id": article_id,
                        "grade": grade,
                        "source": "fixture",
                    }
                )
    (FIXTURES_DIR / "pool_text.json").write_text(json.dumps(texts, indent=1), encoding="utf-8")
    (FIXTURES_DIR / "labels.jsonl").write_text(
        "\n".join(json.dumps(row) for row in labels) + "\n", encoding="utf-8"
    )


class FakeResult:
    """Duck-typed SearchResult stand-in (attribute-compatible subset)."""

    def __init__(
        self, article_id: str, chunk_id: str, title: str, text: str, score: float, rank: int
    ):
        self.article_id = article_id
        self.chunk_id = chunk_id
        self.title = title
        self.source_name = "mock"
        self.similarity_score = score
        self.chunk_index = rank
        self.chunk_text = text
        self.extra_chunks: list[Any] = []


def make_fake_searcher():
    """A deterministic retriever with a different ordering per config.

    The ordering rotates by a hash of (config_id, query) over a fixed
    relevance ladder, so ablation rows differ from each other the way real
    ones do — but identically on every machine and every run.
    """

    def search(
        query: str,
        limit: int = 10,
        mode: str = "semantic",
        config_id: str = "",
        **_: Any,
    ):
        ladder = ["art-alpha", "art-beta", "art-gamma", "art-delta", "art-noise"]
        key = hashlib.sha256(f"{config_id}|{mode}|{query}".encode()).digest()
        shift = key[0] % len(ladder)
        rotated = ladder[shift:] + ladder[:shift]
        # Under keyword mode the noise article climbs (simulates sparse drift).
        if mode == "keyword":
            rotated.remove("art-noise")
            rotated.insert(0, "art-noise")
        results: list[FakeResult] = []
        for rank, article_id in enumerate(rotated[:limit]):
            article = FAKE_ARTICLES[article_id]
            results.append(
                FakeResult(
                    article_id=article_id,
                    chunk_id=_chunk_id(article_id, "fact"),
                    title=article["title"],
                    text=article["fact"],
                    score=round(0.9 - rank * 0.1, 4),
                    rank=rank,
                )
            )
        return results

    return search


def canned_chat_fast(query: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fast-path record: cites its lead chunk, contains the fact."""
    lead = query["relevant_article_ids"][0]
    return {
        "answer": f"Mock answer citing the lead source [#1]. {FAKE_ARTICLES[lead]['fact']}",
        "evidence": [
            {
                "citation_id": 1,
                "chunk_id": _chunk_id(lead, "fact"),
                "article_id": lead,
                "title": FAKE_ARTICLES[lead]["title"],
            }
        ],
        "invalid_citations": [],
        "agreement": 1.0,
        "sub_questions": [],
        "had_first_token": True,
        "error": None,
        "retrieved_chunk_ids": [_chunk_id(a, "fact") for a in FAKE_ARTICLES],
    }


def canned_chat_deep(query: dict[str, Any]) -> dict[str, Any]:
    """Deterministic deep-path record.

    Every third query deliberately drops the second citation (invalid) and
    skips the fact, so refusal/invalid arithmetic is exercised — the T1-deep
    defect shape, reproduced deterministically.
    """
    lead = query["relevant_article_ids"][0]
    drop_fact = query["id"] in {"m003", "m009"}
    answer = "Sub-question analysis based on the sources [#1][#2]."
    if not drop_fact:
        answer += " " + FAKE_ARTICLES[lead]["fact"]
    return {
        "answer": answer,
        "evidence": [
            {
                "citation_id": 1,
                "chunk_id": _chunk_id(lead, "fact"),
                "article_id": lead,
                "title": FAKE_ARTICLES[lead]["title"],
            },
            {
                "citation_id": 2,
                "chunk_id": _chunk_id("art-noise", "fact"),
                "article_id": "art-noise",
                "title": FAKE_ARTICLES["art-noise"]["title"],
            },
        ],
        "invalid_citations": [3] if drop_fact else [],
        "agreement": 0.5,
        "sub_questions": [
            f"Mock sub-question about {lead}",
            "Mock sub-question about unrelated office snacks"
            if drop_fact
            else f"Mock follow-up on {lead}",
        ],
        "had_first_token": True,
        "error": None,
        "retrieved_chunk_ids": [_chunk_id(a, "fact") for a in FAKE_ARTICLES],
    }


class _CannedJudge:
    """Deterministic judge: relevance by keyword overlap, refusal by content."""

    version = "canned@rubric:mock"
    calls = 0
    errors: list[str] = []

    def judge(self, task: str, items: dict[str, str]) -> dict[str, Any]:
        type(self).calls += 1
        if task == "sub_question_relevance":
            query = items.get("USER QUERY", "").lower()
            sub_q = items.get("SUB-QUESTION", "").lower()
            if "unrelated" in sub_q or "snacks" in sub_q:
                return {
                    "score": 0,
                    "reason": "canned: unrelated",
                    "model": "canned",
                    "latency_ms": 0,
                }
            shared = set(query.split()) & set(sub_q.split())
            score = 2 if len(shared) >= 4 else 1
            return {"score": score, "reason": "canned", "model": "canned", "latency_ms": 0}
        if task == "implicit_refusal":
            answer = items.get("ANSWER", "").lower()
            if not answer or "sub-question analysis" in answer and "sources say" not in answer:
                return {"score": 2, "reason": "canned: filler", "model": "canned", "latency_ms": 0}
            return {"score": 0, "reason": "canned: substantive", "model": "canned", "latency_ms": 0}
        return {"score": 0, "reason": "canned: unknown task", "model": "canned", "latency_ms": 0}


def canned_judge() -> _CannedJudge:
    return _CannedJudge()


if __name__ == "__main__":
    write_mock_dataset()
    print(f"mock fixtures written to {FIXTURES_DIR}")
