"""Unit tests for the baseline report assembly (plan §4/§6)."""

from __future__ import annotations

import json
from pathlib import Path

from eval import report

RETRIEVAL = {
    "suite": "retrieval",
    "snapshot_id": "20261011_abc1234",
    "git_sha": "aaaaaaa",
    "n_queries": 60,
    "pool": {"depth": 10, "mean_pool_size": 48.87, "median_pool_size": 49.5},
    "per_config": {
        "semantic-rerank-temporal": {
            "recall@5": 0.6,
            "recall@10": 0.7,
            "mrr@10": 0.8,
            "ndcg@10": 0.565,
        },
        "hybrid-norank-notemporal": {
            "recall@5": 0.7,
            "recall@10": 0.71,
            "mrr@10": 0.9,
            "ndcg@10": 0.598,
        },
    },
}
RAG = {
    "suite": "rag",
    "snapshot_id": "20261011_abc1234",
    "git_sha": "bbbbbbb",
    "mock": False,
    "judge": {"version": "qwen2.5:7b@rubric:deadbeef", "errors": 0},
    "sub_question_relevance": {"mean": 0.9, "judged": 100, "errors": 0},
    "aggregates": {
        "fast": {
            "citation_precision": 0.9,
            "citation_coverage": 0.5,
            "required_fact_recall": 0.8,
            "invalid_citation_rate": 0.0,
            "refusal_rate": 0.1,
            "judge_routed_count": 2,
            "latency_ms": {"cold": 41000, "warm_p50": 9000, "warm_p95": 15000},
        },
        "deep": {
            "citation_precision": 0.5,
            "citation_coverage": 0.4,
            "required_fact_recall": 0.7,
            "invalid_citation_rate": 0.05,
            "refusal_rate": 0.2,
            "judge_routed_count": 3,
            "latency_ms": {"cold": 34000, "warm_p50": 30000, "warm_p95": 60000},
        },
    },
}
CLUSTERING = {
    "suite": "clustering",
    "snapshot_id": "20261011_abc1234",
    "git_sha": "ccccccc",
    "label_set": {
        "file": "eval/event_labels.json",
        "status": "agent_drafted_pending_20pct_review",
        "n_articles": 29,
        "n_events": 18,
    },
    "slow_path": {
        "n_input_articles": 4,
        "ari": None,
        "nmi": None,
        "purity": None,
        "note": "fewer than 2 labelled articles were in the slow-path input",
    },
    "combined_partition": {"ari": 0.107, "nmi": 0.628, "purity": 0.379},
    "fast_path": {
        "precision": 0.357,
        "judgeable_correct": 10,
        "judgeable_matches": 28,
        "threshold": 0.72,
    },
}
QUALITY = {
    "suite": "quality",
    "git_sha": "ddddddd",
    "heldout": {
        "n_chunks": 50,
        "stratification": {"techcrunch": 15, "bbc": 15, "total": 50},
    },
    "labels": {"status": "agent_drafted_pending_review", "self_consistency_disagreements": 2},
    "chunk_level": {
        "precision": 0.94,
        "recall": 0.9,
        "precision_ci95": [0.84, 0.98],
        "recall_ci95": [0.79, 0.96],
        "tp": 45,
        "fp": 3,
        "fn": 5,
    },
    "article_level": {
        "precision": 0.9,
        "recall": 0.88,
        "precision_ci95": [0.78, 0.96],
        "recall_ci95": [0.75, 0.95],
        "tp": 18,
        "fp": 2,
        "fn": 2,
    },
    "trigger": {
        "rule": "revisit BOILERPLATE_THRESHOLD if held-out precision < 0.95",
        "fired": True,
    },
}


def _write_results(tmp_path: Path) -> None:
    (tmp_path / "20261012_aaaaaaa.json").write_text(json.dumps(RETRIEVAL), encoding="utf-8")
    (tmp_path / "20261012_bbbbbbb_rag.json").write_text(json.dumps(RAG), encoding="utf-8")
    (tmp_path / "20261012_ccccccc_clustering.json").write_text(
        json.dumps(CLUSTERING), encoding="utf-8"
    )
    (tmp_path / "20261012_ddddddd_quality.json").write_text(json.dumps(QUALITY), encoding="utf-8")


def test_latest_suite_files_picks_one_file_per_suite(tmp_path):
    _write_results(tmp_path)
    files = report.latest_suite_files(tmp_path)
    assert set(files) == {"retrieval", "rag", "clustering", "quality"}
    # the plain retrieval name must not swallow the suffixed files
    assert files["retrieval"].name == "20261012_aaaaaaa.json"
    assert files["rag"].name == "20261012_bbbbbbb_rag.json"


def test_baseline_keeps_per_suite_payloads_and_hashes_the_queries(tmp_path):
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"id": "q001"}\n', encoding="utf-8")
    baseline = report.build_baseline(
        {"retrieval": RETRIEVAL, "rag": RAG, "clustering": CLUSTERING, "quality": QUALITY},
        queries_path=queries,
    )
    assert baseline["snapshot_id"] == "20261011_abc1234"
    assert baseline["suites_present"] == ["retrieval", "rag", "clustering", "quality"]
    assert baseline["queries_sha256"]
    assert baseline["suite_git_shas"]["retrieval"] == "aaaaaaa"
    assert baseline["suites"]["quality"] is QUALITY  # payloads stored unmodified


def test_baseline_markdown_reports_every_suite(tmp_path):
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"id": "q001"}\n', encoding="utf-8")
    baseline = report.build_baseline(
        {"retrieval": RETRIEVAL, "rag": RAG, "clustering": CLUSTERING, "quality": QUALITY},
        queries_path=queries,
    )
    md = report.render_md(baseline)
    assert "Retrieval ablation" in md
    assert "hybrid-norank-notemporal" in md
    assert "RAG fast vs deep" in md
    assert "qwen2.5:7b@rubric:deadbeef" in md
    assert "Clustering vs the labelled event set" in md
    assert "unscoreable" in md or "fewer than 2" in md
    assert "Content quality" in md
    assert "FIRED" in md
    # a metric with no value must render as an em dash, never as 0
    assert "| slow-path ARI / NMI / purity (labelled members) | — / — / — |" in md


def test_baseline_markdown_renders_undefined_precision_honestly(tmp_path):
    # A suite whose classifier never predicted positive: precision is not 0.000.
    silent = {
        **QUALITY,
        "chunk_level": {
            "precision": None,
            "recall": 0.0,
            "precision_ci95": [0.0, 1.0],
            "recall_ci95": [0.0, 0.39],
            "tp": 0,
            "fp": 0,
            "fn": 6,
        },
        "trigger": {**QUALITY["trigger"], "note": "precision is undefined"},
    }
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"id": "q001"}\n', encoding="utf-8")
    md = report.render_md(report.build_baseline({"quality": silent}, queries_path=queries))
    assert "| chunk | undefined (0.000-1.000) | 0.000 (0.000-0.390) | 0/0/6 |" in md
    assert "precision is undefined" in md


def test_merge_with_existing_lets_this_run_win(tmp_path):
    _write_results(tmp_path)
    stale = {**CLUSTERING, "git_sha": "stale01"}
    (tmp_path / "20261012_ccccccc_clustering.json").write_text(json.dumps(stale), encoding="utf-8")
    merged = report.merge_with_existing({"rag": RAG}, results_dir=tmp_path)
    assert set(merged) == {"retrieval", "rag", "clustering", "quality"}
    assert merged["rag"] is RAG  # this run's payload wins over the on-disk copy
    assert merged["retrieval"]["git_sha"] == "aaaaaaa"


def test_write_baseline_writes_json_md_and_latest(tmp_path):
    _write_results(tmp_path)
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"id": "q001"}\n', encoding="utf-8")
    payloads = {
        suite: json.loads(path.read_text(encoding="utf-8"))
        for suite, path in report.latest_suite_files(tmp_path).items()
    }
    json_path, md_path = report.write_baseline(payloads, results_dir=tmp_path, queries_path=queries)
    assert json_path.exists() and md_path.exists()
    assert json_path.name.endswith("_baseline.json")
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest == json.loads(json_path.read_text(encoding="utf-8"))
    assert md_path.read_text(encoding="utf-8").startswith("# Phase 2 evaluation baseline")
