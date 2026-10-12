"""Unit tests for eval.metrics — the arithmetic behind every ablation table."""

from __future__ import annotations

from eval.metrics import (
    aggregate,
    evaluate_ranking,
    grade2_ids,
    macro_mean,
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
)

GRADES = {
    "a": 2,  # directly answers
    "b": 2,  # directly answers
    "c": 1,  # topically related
    "d": 0,  # irrelevant
}


def test_grade2_ids():
    assert grade2_ids(GRADES) == {"a", "b"}
    assert grade2_ids({}) == set()


def test_recall_partial_and_full():
    assert recall_at_k(["a", "d", "c"], GRADES, 3) == 0.5  # only "a" in top-3
    assert recall_at_k(["d", "c", "a", "b"], GRADES, 4) == 1.0
    assert recall_at_k(["c", "d"], GRADES, 2) == 0.0  # no grade-2 in top-2


def test_recall_ignores_grade1_and_unjudged():
    # "c" (grade 1) and "x" (unjudged -> 0) must not count toward recall
    assert recall_at_k(["c", "x", "d"], GRADES, 3) == 0.0


def test_recall_undefined_without_grade2():
    assert recall_at_k(["c", "d"], {"c": 1, "d": 0}, 2) is None
    assert recall_at_k([], {}, 10) is None


def test_mrr_first_hit_position():
    assert mrr_at_k(["d", "a", "b"], GRADES, 10) == 0.5  # first grade-2 at rank 2
    assert mrr_at_k(["a", "b"], GRADES, 10) == 1.0  # rank 1
    assert mrr_at_k(["d", "c"], GRADES, 10) == 0.0  # no hit -> 0.0, not None
    assert mrr_at_k(["d", "a"], {"d": 0, "a": 1}, 10) is None  # no grade-2 judged


def test_mrr_respects_k():
    assert mrr_at_k(["d", "d", "d", "d", "d", "d", "d", "d", "d", "a"], GRADES, 10) == 0.1
    assert mrr_at_k(["d", "d", "d", "d", "d", "d", "d", "d", "d", "a"], GRADES, 5) == 0.0


def test_ndcg_perfect_ranking_is_one():
    assert ndcg_at_k(["a", "b", "c", "d"], GRADES, 10) == 1.0


def test_ndcg_grades_matter_not_just_presence():
    # Putting the grade-1 chunk above the grade-2s must hurt vs. ideal order.
    ideal = ndcg_at_k(["a", "b", "c", "d"], GRADES, 10)
    swapped = ndcg_at_k(["c", "a", "b", "d"], GRADES, 10)
    assert 0.0 < swapped < ideal == 1.0


def test_ndcg_unjudged_chunks_gain_zero():
    # An unjudged chunk ("x") in rank 1 contributes nothing.
    with_unjudged_first = ndcg_at_k(["x", "a", "b"], GRADES, 10)
    assert with_unjudged_first < 1.0
    assert with_unjudged_first > 0.0  # the grade-2s still earn some gain


def test_ndcg_all_zero_ideal_is_zero_by_convention():
    assert ndcg_at_k(["d", "c"], {"d": 0, "c": 0}, 10) == 0.0
    assert ndcg_at_k([], {}, 10) == 0.0


def test_ndcg_respects_k():
    # Ideal gain sits entirely outside the cutoff -> nDCG@3 is 0.
    ranked = ["d", "d", "d", "a"]
    assert ndcg_at_k(ranked, {"d": 0, "a": 2}, 3) == 0.0
    assert ndcg_at_k(ranked, {"d": 0, "a": 2}, 4) > 0.0


def test_evaluate_ranking_keys():
    metrics = evaluate_ranking(["a", "c", "d"], GRADES)
    assert set(metrics) == {"recall@5", "recall@10", "mrr@10", "ndcg@10"}
    assert metrics["recall@5"] == 0.5
    assert metrics["mrr@10"] == 1.0


def test_macro_mean_skips_none():
    assert macro_mean([0.5, None, 1.0]) == 0.75
    assert macro_mean([None, None]) is None
    assert macro_mean([]) is None


def test_aggregate_reports_denominators():
    per_query = [
        {"recall@10": 1.0, "mrr@10": 1.0},
        {"recall@10": None, "mrr@10": 0.0},  # undefined recall query excluded
    ]
    agg = aggregate(per_query)
    assert agg["recall@10"] == 1.0
    assert agg["recall@10_n"] == 1
    assert agg["mrr@10"] == 0.5
    assert agg["mrr@10_n"] == 2
