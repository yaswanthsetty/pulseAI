"""Unit tests for the eval harness plumbing (pooling, grading, stats, judge, CI)."""

from __future__ import annotations

import json

from eval.grading import draft_grade
from eval.judge import Judge, parse_judgment
from eval.pooling import build_blinded_pool, pool_stats
from eval.stats import precision_recall, wilson_interval

# --- pooling ---------------------------------------------------------------


def test_pool_is_blinded_shuffle_of_the_union():
    surfaced = {"a", "b", "c"}
    picks = {"c", "d"}
    pool = build_blinded_pool(surfaced, picks, seed_key="s:q1")
    assert sorted(entry["chunk_id"] for entry in pool) == ["a", "b", "c", "d"]
    assert [entry["pool_index"] for entry in pool] == [0, 1, 2, 3]


def test_pool_is_deterministic_per_seed():
    first = build_blinded_pool({"a", "b", "c", "d", "e"}, set(), seed_key="s:q1")
    second = build_blinded_pool({"a", "b", "c", "d", "e"}, set(), seed_key="s:q1")
    assert first == second
    different = build_blinded_pool({"a", "b", "c", "d", "e"}, set(), seed_key="s:q2")
    assert [e["chunk_id"] for e in first] != [e["chunk_id"] for e in different]


def test_pool_shuffled_not_sorted():
    ids = {f"c{i:02d}" for i in range(30)}
    pool = build_blinded_pool(ids, set(), seed_key="shuffle-check")
    order = [e["chunk_id"] for e in pool]
    assert order != sorted(order)  # shuffled, while remaining deterministic


def test_pool_stats():
    pools = {
        "q1": [{"pool_index": 0, "chunk_id": "a"}],
        "q2": [{"pool_index": i, "chunk_id": str(i)} for i in range(5)],
    }
    stats = pool_stats(pools)
    assert stats == {
        "queries": 2,
        "mean_pool_size": 3.0,
        "median_pool_size": 3.0,
        "min_pool_size": 1,
        "max_pool_size": 5,
    }


# --- grading ---------------------------------------------------------------


def test_draft_grade_fact_beats_pick():
    grade, source = draft_grade(
        "the screen is a 6.83in OLED panel",
        ["6.83in OLED"],
        is_labeler_pick_chunk=False,
        is_labeler_pick_article=False,
    )
    assert (grade, source) == (2, "rule_fact")


def test_draft_grade_pick_article_without_fact():
    grade, source = draft_grade(
        "a mid-range phone with an aluminium frame",
        ["6.83in OLED"],
        is_labeler_pick_chunk=False,
        is_labeler_pick_article=True,
    )
    assert (grade, source) == (1, "rule_pick")


def test_draft_grade_pick_chunk_without_fact():
    grade, source = draft_grade(
        "a mid-range phone",
        ["6.83in OLED"],
        is_labeler_pick_chunk=True,
        is_labeler_pick_article=False,
    )
    assert (grade, source) == (1, "rule_pick")


def test_draft_grade_zero_otherwise():
    grade, source = draft_grade(
        "the cafeteria serves trout on Fridays",
        ["6.83in OLED"],
        is_labeler_pick_chunk=False,
        is_labeler_pick_article=False,
    )
    assert (grade, source) == (0, "rule_zero")


def test_draft_grade_fact_match_is_case_insensitive():
    grade, _ = draft_grade(
        "It costs $499 in the US.",
        ["$499"],
        is_labeler_pick_chunk=False,
        is_labeler_pick_article=False,
    )
    assert grade == 2


# --- stats -----------------------------------------------------------------


def test_wilson_interval_known_value():
    low, high = wilson_interval(48, 50)
    assert 0.86 < low < 0.94
    assert 0.98 < high <= 1.0


def test_wilson_interval_degenerate():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 30)
    assert low == 0.0
    assert high < 0.2


def test_precision_recall_with_intervals():
    result = precision_recall(
        predicted_positive=[True, True, False, False, True],
        ground_truth=[True, False, True, False, True],
    )
    assert result["tp"] == 2
    assert result["fp"] == 1
    assert result["fn"] == 1
    assert result["precision"] == 2 / 3
    assert result["recall"] == 2 / 3
    low, high = result["precision_ci95"]
    assert 0.0 <= low < 2 / 3 < high <= 1.0


# --- judge parsing ---------------------------------------------------------


def test_parse_judgment_strict_json():
    assert parse_judgment('{"score": 2, "reason": "yes"}') == {"score": 2, "reason": "yes"}


def test_parse_judgment_fenced_and_wrapped():
    assert parse_judgment('```json\n{"score": 0, "reason": "no"}\n```')["score"] == 0
    assert parse_judgment('Sure! {"score": 1, "reason": "mid"} done.')["score"] == 1


def test_parse_judgment_rejects_bad_scores():
    import pytest

    with pytest.raises(ValueError):
        parse_judgment('{"score": 5, "reason": "out of range"}')
    with pytest.raises(ValueError):
        parse_judgment("no json here")


def test_judge_refuses_model_under_test():
    import pytest

    with pytest.raises(ValueError):
        Judge(model="qwen2.5:3b", transport=lambda *_: "{}")
    with pytest.raises(ValueError):
        Judge(fallback_model="qwen2.5:3b", transport=lambda *_: "{}")


def test_judge_falls_back_on_slow_first_call():
    calls: list[str] = []

    def slow_then_fast(model: str, _system: str, _user: str) -> str:
        calls.append(model)
        if model == "qwen2.5:14b":
            return '{"score": 1, "reason": "slow but valid"}'
        return '{"score": 1, "reason": "fast"}'

    judge = Judge(slow_seconds=0.0, transport=slow_then_fast)  # every call "slow"
    result = judge.judge("implicit_refusal", {"QUESTION": "q", "ANSWER": "a"})
    # First call triggers the fallback decision; the item is re-judged with 7b.
    assert judge.fallback_used is True
    assert result["model"] == "qwen2.5:7b"
    assert "qwen2.5:7b" in calls


def test_judge_records_errors_and_returns_none():
    def broken(*_args: str) -> str:
        raise RuntimeError("ollama down")

    judge = Judge(transport=broken)
    result = judge.judge("implicit_refusal", {"QUESTION": "q", "ANSWER": "a"})
    assert result["score"] is None
    assert len(judge.errors) == 2  # two attempts, both recorded


def test_cohen_kappa_perfect_and_chance():
    judge = Judge(transport=lambda *_: "{}")
    perfect = judge.agreement([({"score": 2}, 2), ({"score": 0}, 0), ({"score": 1}, 1)])
    assert perfect["raw_agreement"] == 1.0
    assert perfect["kappa"] == 1.0
    none = judge.agreement([])
    assert none["n"] == 0


# --- CI subset determinism -------------------------------------------------


def test_ci_subset_is_deterministic_and_complete():
    from eval import ci_subset

    first = ci_subset.run()
    second = ci_subset.run()
    assert first == second  # exact determinism is what CI asserts
    assert first["n_queries"] == 12
    types = {
        q["type"] for q in __import__("eval.mock", fromlist=["fixture_queries"]).fixture_queries()
    }
    assert types == {"fact", "compare", "multi-hop", "time-bound"}
    paths = {
        q["path_hint"]
        for q in __import__("eval.mock", fromlist=["fixture_queries"]).fixture_queries()
    }
    assert paths == {"fast", "deep"}
    # 12 ablation configs exercised
    assert len(first["retrieval_per_config"]) == 12
    # the deliberate deep-path defects are visible as numbers
    assert first["rag"]["deep"]["refusal_count"] > 0
    assert first["sub_question_relevance"] is not None


def test_ci_baseline_is_current():
    """The committed baseline must match the harness output exactly.

    If this fails, the harness arithmetic changed — regenerate the baseline in
    its own reviewed commit: uv run python -m eval.ci_subset --write-baseline
    """
    from eval import ci_subset

    baseline = json.loads(ci_subset.BASELINE_PATH.read_text(encoding="utf-8"))
    assert ci_subset.run() == baseline
