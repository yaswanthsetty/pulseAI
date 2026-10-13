"""Binomial proportion intervals (plan §2.4: 95% Wilson, never hidden)."""

from __future__ import annotations

from math import sqrt

Z_95 = 1.96


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Returns ``(low, high)``; at n=0 returns ``(0.0, 1.0)`` (fully uncertain).
    """
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    margin = (z / denominator) * sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def precision_recall(
    predicted_positive: list[bool], ground_truth: list[bool]
) -> dict[str, float | int | tuple[float, float] | None]:
    """Precision/recall with Wilson intervals (positive = boilerplate).

    ``precision`` is ``None`` when nothing was predicted positive: with an
    empty denominator precision is undefined, and reporting it as 0.000 would
    read as "every prediction was wrong" when in fact there were no
    predictions at all.
    """
    if len(predicted_positive) != len(ground_truth):
        raise ValueError("length mismatch")
    tp = sum(
        1 for pred, truth in zip(predicted_positive, ground_truth, strict=True) if pred and truth
    )
    fp = sum(
        1
        for pred, truth in zip(predicted_positive, ground_truth, strict=True)
        if pred and not truth
    )
    fn = sum(
        1
        for pred, truth in zip(predicted_positive, ground_truth, strict=True)
        if not pred and truth
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "precision_ci95": wilson_interval(tp, tp + fp),
        "recall": recall,
        "recall_ci95": wilson_interval(tp, tp + fn),
    }
