"""Unit tests for eval.stats — Wilson intervals and the paired bootstrap."""

from __future__ import annotations

import pytest
from eval.stats import (
    BOOTSTRAP_SEED,
    paired_bootstrap_delta,
    percentile,
    wilson_interval,
)


def test_wilson_interval_bounds_and_edges():
    assert wilson_interval(0, 0) == (0.0, 1.0)  # no data is not a zero interval
    low, high = wilson_interval(63, 65)
    assert 0.88 < low < 0.97 < high <= 1.0
    assert wilson_interval(0, 10)[0] == 0.0


def test_percentile_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 0.5) == 3.0
    assert percentile(values, 1.0) == 5.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_bootstrap_delta_is_the_paired_mean_difference():
    a = [1.0, 1.0, 0.0, 1.0]
    b = [0.0, 0.0, 0.0, 1.0]
    result = paired_bootstrap_delta(a, b, resamples=500)
    assert result["n"] == 4
    assert result["delta"] == pytest.approx(0.5)
    assert result["dropped"] == 0
    low, high = result["ci95"]
    assert low <= result["delta"] <= high


def test_bootstrap_marks_a_one_sided_improvement_and_not_a_tie():
    clearly_better = paired_bootstrap_delta([1.0] * 20, [0.0] * 20, resamples=500)
    assert clearly_better["excludes_zero"] is True
    tied = paired_bootstrap_delta([0.5] * 20, [0.5] * 20, resamples=500)
    assert tied["delta"] == 0.0
    assert tied["excludes_zero"] is False
    # a difference that changes sign across queries must not be called one
    mixed = paired_bootstrap_delta([1.0, 0.0, 1.0, 0.0] * 5, [0.0, 1.0, 0.0, 1.0] * 5)
    assert mixed["excludes_zero"] is False


def test_bootstrap_is_reproducible_and_drops_undefined_queries():
    a = [1.0, None, 0.5, 0.25]
    b = [0.0, 1.0, None, 0.0]
    first = paired_bootstrap_delta(a, b, resamples=1000)
    second = paired_bootstrap_delta(a, b, resamples=1000)
    assert first == second  # fixed seed: a committed interval must not move
    assert first["n"] == 2  # only the pairs both sides define
    assert first["dropped"] == 2
    assert first["seed"] == BOOTSTRAP_SEED


def test_bootstrap_with_no_usable_pairs_is_undefined_not_zero():
    result = paired_bootstrap_delta([None, None], [0.0, 0.0])
    assert result["n"] == 0
    assert result["delta"] is None
    assert result["ci95"] is None
    assert result["excludes_zero"] is False


def test_bootstrap_rejects_length_mismatch():
    with pytest.raises(ValueError):
        paired_bootstrap_delta([1.0], [1.0, 0.0])
