"""Unit tests for eval.retrieval_suite — significance and query-set filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval import retrieval_suite as RS


def _records(ndcg: list[float], recall: list[float]) -> list[dict[str, float]]:
    return [{"ndcg@10": n, "recall@10": r} for n, r in zip(ndcg, recall, strict=True)]


def _per_config() -> dict[str, list[dict[str, float]]]:
    n = 10
    return {
        RS.PRODUCTION_CONFIG: _records([0.5] * n, [0.6] * n),
        "semantic-norank-notemporal": _records([0.4] * n, [0.5] * n),
        "hybrid-rerank-temporal": _records([0.9] * n, [1.0] * n),
    }


def test_significance_reports_every_config_against_the_production_default():
    significance = RS.significance_vs_default(_per_config())
    assert set(significance) == {"ndcg@10", "recall@10"}
    assert RS.PRODUCTION_CONFIG not in significance["ndcg@10"]  # never against itself
    worse = significance["ndcg@10"]["semantic-norank-notemporal"]
    better = significance["ndcg@10"]["hybrid-rerank-temporal"]
    assert worse["delta"] == pytest.approx(-0.1)
    assert better["delta"] == pytest.approx(0.4)
    assert worse["excludes_zero"] is True  # a consistent gap over every query
    assert better["excludes_zero"] is True
    assert worse["n"] == 10


def test_significance_needs_a_baseline_to_compare_against():
    assert RS.significance_vs_default({"semantic-norank-temporal": _records([1.0], [1.0])}) == {}


def test_significance_lines_mark_only_the_intervals_that_exclude_zero():
    payload = {
        "significance": {"baseline_config": RS.PRODUCTION_CONFIG, "resamples": 10_000, "seed": 7}
    }
    payload["significance"]["metrics"] = RS.significance_vs_default(_per_config())
    lines = "\n".join(RS.significance_lines(payload))
    assert "Paired bootstrap vs the production default" in lines
    assert "delta nDCG@10" in lines  # ASCII header: a cp1252 console must print it
    assert "| semantic-norank-notemporal | -0.100 (-0.100 to -0.100) | **yes** |" in lines
    assert "| hybrid-rerank-temporal | +0.400" in lines


def test_significance_lines_are_empty_without_the_block():
    assert RS.significance_lines({}) == []


def test_noise_does_not_get_called_a_difference():
    """A delta that flips sign across queries must not exclude 0."""
    noisy = {
        RS.PRODUCTION_CONFIG: _records([0.5, 0.5, 0.5, 0.5], [0.0, 0.0, 0.0, 0.0]),
        "hybrid-norank-temporal": _records([0.9, 0.1, 0.9, 0.1], [0.0, 0.0, 0.0, 0.0]),
    }
    entry = RS.significance_vs_default(noisy)["ndcg@10"]["hybrid-norank-temporal"]
    assert entry["excludes_zero"] is False


def _write_queries(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_load_queries_excludes_rejected_rows_but_can_still_describe_the_file(tmp_path, monkeypatch):
    path = tmp_path / "queries.jsonl"
    _write_queries(
        path,
        [
            {"id": "q001", "snapshot": "snap", "status": "reviewed"},
            {"id": "q002", "snapshot": "snap", "status": "rejected"},
        ],
    )
    monkeypatch.setattr(RS, "QUERIES_PATH", path)
    assert [q["id"] for q in RS.load_queries("snap")] == ["q001"]
    assert [q["id"] for q in RS.load_queries("snap", include_rejected=True)] == ["q001", "q002"]


def test_load_queries_refuses_a_fully_rejected_set(tmp_path, monkeypatch):
    path = tmp_path / "queries.jsonl"
    _write_queries(path, [{"id": "q001", "snapshot": "snap", "status": "rejected"}])
    monkeypatch.setattr(RS, "QUERIES_PATH", path)
    with pytest.raises(SystemExit, match="rejected"):
        RS.load_queries("snap")


def test_load_queries_still_refuses_a_snapshot_mismatch(tmp_path, monkeypatch):
    path = tmp_path / "queries.jsonl"
    _write_queries(path, [{"id": "q001", "snapshot": "other", "status": "reviewed"}])
    monkeypatch.setattr(RS, "QUERIES_PATH", path)
    with pytest.raises(SystemExit, match="not on snapshot"):
        RS.load_queries("snap")
