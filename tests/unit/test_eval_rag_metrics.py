"""Unit tests for the RAG metric set (plan §2.2) — hand-computed expectations.

The CI subset exercises ``_metric_set`` end to end with canned records; these
tests pin the arithmetic itself, including the cases where a metric is
*undefined* (no cited ids, no grade-2 evidence) and must come back as None
rather than 0 or 1.
"""

from __future__ import annotations

from typing import Any

from eval.rag_suite import _metric_set, _refusal_breakdown

GRADES = {"chunk-a": 2, "chunk-b": 0, "chunk-c": 2}


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "query_id": "q001",
        "path": "fast",
        "message": "What throughput did AWS report?",
        "answer": "AWS reported 66% [#1][#2].",
        "evidence": [
            {"citation_id": 1, "chunk_id": "chunk-a", "article_id": "art-a", "title": "A"},
            {"citation_id": 2, "chunk_id": "chunk-b", "article_id": "art-b", "title": "B"},
        ],
        "invalid_citations": [],
        "retrieved_chunk_ids": ["chunk-a", "chunk-b", "chunk-c"],
    }
    record.update(overrides)
    return record


class _StubJudge:
    """Judge stub that records what it was asked and returns a fixed score."""

    def __init__(self, score: int | None = 2) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.score = score

    def judge(self, task: str, items: dict[str, str]) -> dict[str, Any]:
        self.calls.append((task, items))
        return {"score": self.score, "reason": "stub", "model": "stub", "latency_ms": 1}


def test_refusal_breakdown_separates_missing_evidence_from_stubbornness():
    # Refusing with no grade-2 evidence retrieved is correct; refusing with
    # evidence retrieved is T1-deep. Blending them hides both.
    rows = [
        {"n_grade2_retrieved": 0, "refusal": True},
        {"n_grade2_retrieved": 2, "refusal": True},
        {"n_grade2_retrieved": 1, "refusal": False},
        {"n_grade2_retrieved": 3, "refusal": False},
        {"n_grade2_retrieved": 0, "refusal": False},
    ]
    breakdown = _refusal_breakdown(rows)
    assert breakdown["n_with_evidence"] == 3
    assert breakdown["refusal_when_evidence_exists_rate"] == 1 / 3
    assert breakdown["n_without_evidence"] == 2
    assert breakdown["refusal_without_evidence_rate"] == 0.5


def test_refusal_breakdown_is_none_when_a_side_is_empty():
    only_with = _refusal_breakdown([{"n_grade2_retrieved": 1, "refusal": False}])
    assert only_with["refusal_when_evidence_exists_rate"] == 0.0
    assert only_with["refusal_without_evidence_rate"] is None
    assert _refusal_breakdown([])["refusal_when_evidence_exists_rate"] is None


def test_citation_precision_over_resolved_citations_only():
    metrics = _metric_set(_record(), GRADES, ["66%"], None)
    # [#1] resolves to a grade-2 chunk, [#2] to a grade-0 chunk -> 1/2.
    assert metrics["citation_precision"] == 0.5
    assert metrics["n_cited"] == 2


def test_unresolved_citations_do_not_enter_citation_precision():
    record = _record(answer="AWS reported 66% [#1][#9].", invalid_citations=[])
    metrics = _metric_set(record, GRADES, ["66%"], None)
    # [#9] has no evidence item: it is dropped from precision, not counted as
    # a miss (invalid-citation rate is where broken ids are measured).
    assert metrics["citation_precision"] == 1.0


def test_citation_precision_is_none_when_nothing_resolves():
    record = _record(answer="Answer with no citations at all.")
    metrics = _metric_set(record, GRADES, ["66%"], None)
    assert metrics["citation_precision"] is None


def test_citation_coverage_is_over_grade2_retrieved_chunks():
    metrics = _metric_set(_record(), GRADES, ["66%"], None)
    # grade-2 retrieved = {chunk-a, chunk-c}; cited chunks = {chunk-a, chunk-b}.
    assert metrics["n_grade2_retrieved"] == 2
    assert metrics["citation_coverage"] == 0.5


def test_citation_coverage_is_none_without_grade2_evidence():
    record = _record(retrieved_chunk_ids=["chunk-b"])
    metrics = _metric_set(record, GRADES, ["66%"], None)
    assert metrics["citation_coverage"] is None


def test_required_fact_recall_is_fractional():
    metrics = _metric_set(_record(answer="AWS reported 66% [#1]."), GRADES, ["66%", "2027"], None)
    assert metrics["required_fact_recall"] == 0.5
    assert _metric_set(_record(), GRADES, ["66%"], None)["required_fact_recall"] == 1.0


def test_invalid_citation_rate_over_all_attempts():
    record = _record(answer="AWS reported 66% [#1].", invalid_citations=[2, 3])
    metrics = _metric_set(record, GRADES, ["66%"], None)
    # 1 valid citation + 2 stripped ids -> 2/3.
    assert metrics["invalid_citation_rate"] == 2 / 3


def test_invalid_citation_rate_is_none_without_attempts():
    metrics = _metric_set(_record(answer="No citation markers here."), GRADES, ["66%"], None)
    assert metrics["invalid_citation_rate"] is None


def test_explicit_refusal_is_detected_without_judging():
    judge = _StubJudge()
    record = _record(answer="I don't have enough information to answer that.")
    metrics = _metric_set(record, GRADES, ["66%"], judge)
    assert metrics["refusal"] is True
    assert metrics["refusal_verdict"] == "refusal"
    assert metrics["judge_routed"] is False
    assert judge.calls == []  # the deterministic list decides; no LLM call


def test_fact_present_is_not_a_refusal_even_with_evidence():
    judge = _StubJudge()
    metrics = _metric_set(_record(), GRADES, ["66%"], judge)
    assert metrics["refusal"] is False
    assert metrics["judge_routed"] is False


def test_hedge_with_grade2_evidence_is_routed_to_the_judge_and_counted():
    judge = _StubJudge(score=2)
    record = _record(answer="The sources discuss the migration in general terms only.")
    metrics = _metric_set(record, GRADES, ["66%"], judge)
    assert metrics["refusal_verdict"] == "borderline"
    assert metrics["judge_routed"] is True
    assert metrics["judge_score"] == 2
    assert metrics["refusal"] is True
    assert [task for task, _ in judge.calls] == ["implicit_refusal"]


def test_borderline_case_without_evidence_is_not_refusal_routed():
    judge = _StubJudge(score=2)
    record = _record(
        answer="The sources discuss the migration in general terms only.",
        retrieved_chunk_ids=["chunk-b"],
    )
    metrics = _metric_set(record, GRADES, ["66%"], judge)
    assert metrics["refusal_verdict"] == "ok"
    assert metrics["judge_routed"] is False
    assert judge.calls == []


def test_judge_cache_serves_repeats_from_memory_and_disk(tmp_path):
    from eval.rag_suite import _JudgeCache

    calls: list[str] = []

    def judge(task: str, items: dict[str, str]) -> dict[str, Any]:
        calls.append(task)
        return {"score": 2, "reason": "x", "model": "m", "latency_ms": 1}

    path = tmp_path / "cache.json"
    cache = _JudgeCache(path)
    wrapped = cache.wrap(judge)
    first = wrapped("sub_question_relevance", {"A": "1", "B": "2"})
    second = wrapped("sub_question_relevance", {"B": "2", "A": "1"})  # same key
    assert first == second
    assert calls == ["sub_question_relevance"]  # the model was asked once
    assert cache.hits == 1

    cache.save()
    fresh = _JudgeCache(path)
    assert fresh.wrap(judge)("sub_question_relevance", {"A": "1", "B": "2"}) == first
    assert calls == ["sub_question_relevance"]  # served from disk after a restart

    wrapped("implicit_refusal", {"A": "1", "B": "2"})
    assert len(calls) == 2  # a different task is a different question


def test_judge_error_is_recorded_as_no_refusal_not_as_a_crash():
    metrics = _metric_set(
        _record(answer="The sources discuss the migration in general terms only."),
        GRADES,
        ["66%"],
        _StubJudge(score=None),
    )
    assert metrics["judge_routed"] is True
    assert metrics["judge_score"] is None
    assert metrics["refusal"] is False
