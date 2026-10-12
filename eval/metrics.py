"""Retrieval metrics over graded (2/1/0) relevance labels.

Pure functions with no backend imports so they can be unit-tested without
infrastructure. Conventions (also stated in docs/plans/phase2_eval.md):

* ``grades`` maps chunk_id -> grade in {0, 1, 2}. Chunks absent from the
  mapping are **unjudged and count as 0**.
* Recall@k / MRR@k are defined over the **grade-2** (directly answering)
  chunks; queries with no grade-2 chunk in the judged set are *undefined*
  for those metrics and are excluded from the macro-average (reported as a
  count, never silently coerced to 0).
* nDCG@k is well-defined for every query: with an all-zero ideal DCG it is
  0.0 by convention.
"""

from __future__ import annotations

from math import log2

GRADES = (0, 1, 2)


def grade2_ids(grades: dict[str, int]) -> set[str]:
    """The directly-answering chunk ids in a judged set."""
    return {cid for cid, g in grades.items() if g == 2}


def recall_at_k(ranked: list[str], grades: dict[str, int], k: int) -> float | None:
    """Fraction of grade-2 chunks present in the top-``k`` of ``ranked``.

    Returns ``None`` when the judged set has no grade-2 chunk (undefined;
    excluded from macro-averages by :func:`macro_mean`).
    """
    relevant = grade2_ids(grades)
    if not relevant:
        return None
    top = set(ranked[:k])
    return len(relevant & top) / len(relevant)


def mrr_at_k(ranked: list[str], grades: dict[str, int], k: int = 10) -> float | None:
    """Reciprocal rank of the first grade-2 hit within the top-``k``.

    ``None`` when the judged set has no grade-2 chunk.
    """
    relevant = grade2_ids(grades)
    if not relevant:
        return None
    for position, chunk_id in enumerate(ranked[:k], start=1):
        if chunk_id in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: list[str], grades: dict[str, int], k: int = 10) -> float:
    """Graded nDCG@``k`` with binary-log discount; unjudged chunks gain 0."""

    def dcg(sequence: list[str]) -> float:
        total = 0.0
        for position, chunk_id in enumerate(sequence[:k], start=1):
            gain = 2 ** grades.get(chunk_id, 0) - 1
            total += gain / log2(position + 1)
        return total

    ideal_order = sorted(grades, key=lambda cid: grades[cid], reverse=True)
    ideal = dcg(ideal_order)
    if ideal == 0.0:
        return 0.0
    return dcg(ranked) / ideal


def macro_mean(values: list[float | None]) -> float | None:
    """Mean over the defined values; ``None`` when nothing is defined."""
    defined = [v for v in values if v is not None]
    if not defined:
        return None
    return sum(defined) / len(defined)


def evaluate_ranking(
    ranked: list[str], grades: dict[str, int], ks: tuple[int, ...] = (5, 10)
) -> dict[str, float | None]:
    """All retrieval metrics for one (query, config) ranking."""
    out: dict[str, float | None] = {}
    for k in ks:
        out[f"recall@{k}"] = recall_at_k(ranked, grades, k)
    out["mrr@10"] = mrr_at_k(ranked, grades, 10)
    out["ndcg@10"] = ndcg_at_k(ranked, grades, 10)
    return out


def aggregate(
    per_query: list[dict[str, float | None]],
) -> dict[str, float | None | int]:
    """Macro-average per-query metrics, reporting the denominator per metric."""
    keys = per_query[0].keys() if per_query else []
    agg: dict[str, float | None | int] = {}
    for key in keys:
        values = [record[key] for record in per_query]
        agg[key] = macro_mean(values)
        agg[f"{key}_n"] = len([v for v in values if v is not None])
    return agg
