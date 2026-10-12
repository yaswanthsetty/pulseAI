"""Unit tests for the held-out content-quality suite (plan §2.4)."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from eval import quality_suite as Q


def _piece(source: str, index: int) -> dict[str, Any]:
    return {
        "sample_id": f"{source}-{index}",
        "article_id": f"art-{source}-{index}",
        "source_name": source,
        "title": "t",
        "extractor": "trafilatura",
        "extraction_quality": "ok",
        "chunk_number": index,
        "token_count": 10,
        "text": "text",
    }


# --- stratified sampling ---------------------------------------------------


def test_sample_takes_at_least_15_from_each_junk_heavy_source():
    by_source = {
        "TechCrunch AI": [_piece("TechCrunch AI", i) for i in range(20)],
        "BBC Science & Tech": [_piece("BBC Science & Tech", i) for i in range(20)],
        "The Verge": [_piece("The Verge", i) for i in range(30)],
    }
    sample = Q._stratified_sample(by_source, random.Random(Q.SAMPLE_SEED))
    assert len(sample) == Q.TARGET_SIZE
    tc = sum(1 for piece in sample if piece["source_name"] == "TechCrunch AI")
    bbc = sum(1 for piece in sample if piece["source_name"] == "BBC Science & Tech")
    assert tc >= Q.JUNK_HEAVY_MIN and bbc >= Q.JUNK_HEAVY_MIN
    assert len({piece["sample_id"] for piece in sample}) == len(sample)  # no duplicates


def test_sample_is_deterministic_for_a_seed_and_changes_with_another():
    by_source = {"TechCrunch AI": [_piece("TechCrunch AI", i) for i in range(40)]}
    first = Q._stratified_sample(by_source, random.Random(Q.SAMPLE_SEED))
    again = Q._stratified_sample(by_source, random.Random(Q.SAMPLE_SEED))
    other = Q._stratified_sample(by_source, random.Random(Q.SAMPLE_SEED + 1))
    assert [piece["sample_id"] for piece in first] == [piece["sample_id"] for piece in again]
    assert [piece["sample_id"] for piece in first] != [piece["sample_id"] for piece in other]


def test_sample_fills_from_other_sources_when_junk_heavy_is_thin():
    by_source = {
        "TechCrunch AI": [_piece("TechCrunch AI", i) for i in range(3)],
        "The Guardian Technology": [_piece("The Guardian", i) for i in range(60)],
    }
    sample = Q._stratified_sample(by_source, random.Random(Q.SAMPLE_SEED))
    assert len(sample) == Q.TARGET_SIZE  # filled from the Guardian to reach 50
    assert sum(1 for piece in sample if piece["source_name"] == "The Guardian") == 47


# --- scoring ---------------------------------------------------------------


def _write_heldout(path, pieces: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-10-12T00:00:00+00:00",
                "sampling_unit": "pre-filter chunks (every piece the classifier sees)",
                "n_candidate_chunks": 4,
                "poll_log": [{"source": "TechCrunch AI", "status": "ok", "new_articles": 2}],
                "stratification": {"techcrunch": 2, "bbc": 2, "total": len(pieces)},
                "sample": pieces,
            }
        ),
        encoding="utf-8",
    )


def test_score_computes_precision_recall_and_counts_disagreements(tmp_path, monkeypatch):
    import backend.core.content_quality as content_quality

    # extraction_quality is an article-level column: both pieces of an article
    # carry the same value, exactly as the real data does.
    pieces = [
        {**_piece("TechCrunch AI", 0), "text": "JUNK nav links", "article_id": "art-a"},
        {**_piece("TechCrunch AI", 1), "text": "JUNK footer", "article_id": "art-a"},
        {
            **_piece("BBC Science & Tech", 0),
            "text": "Real prose here.",
            "article_id": "art-b",
            "extraction_quality": "low",
        },
        {
            **_piece("BBC Science & Tech", 1),
            "text": "More prose.",
            "article_id": "art-b",
            "extraction_quality": "low",
        },
    ]
    labels = {
        "_meta": {"status": "agent_drafted_pending_review"},
        "TechCrunch AI-0": {"pass1": True, "pass2": True, "final": True},
        "TechCrunch AI-1": {"pass1": True, "pass2": False, "final": False},
        "BBC Science & Tech-0": {"pass1": False, "pass2": False, "final": False},
        "BBC Science & Tech-1": {"pass1": False, "pass2": False, "final": True},
    }
    monkeypatch.setattr(Q, "HELDOUT_PATH", tmp_path / "heldout_set.json")
    monkeypatch.setattr(Q, "LABELS_PATH", tmp_path / "heldout_labels.json")
    monkeypatch.setattr(Q, "RESULTS_DIR", tmp_path)
    _write_heldout(Q.HELDOUT_PATH, pieces)
    Q.LABELS_PATH.write_text(json.dumps(labels), encoding="utf-8")
    monkeypatch.setattr(content_quality, "is_boilerplate", lambda text: text.startswith("JUNK"))
    monkeypatch.setattr(content_quality, "boilerplate_score", lambda text: 1.0)

    results = Q.score()

    # predictions [T, T, F, F] vs truth [T, F, F, T] -> tp=1, fp=1, fn=1
    chunk = results["chunk_level"]
    assert (chunk["tp"], chunk["fp"], chunk["fn"]) == (1, 1, 1)
    assert chunk["precision"] == 0.5 and chunk["recall"] == 0.5
    assert chunk["precision_ci95"][0] < 0.5 < chunk["precision_ci95"][1]
    assert results["trigger"]["fired"] is True  # precision < 0.95 -> revisit threshold
    assert results["labels"]["self_consistency_disagreements"] == 1
    assert results["labels"]["disagreement_chunks"] == ["TechCrunch AI-1"]
    # article-level: art-a is predicted ok yet holds a boilerplate piece (fn),
    # art-b is flagged low and does hold boilerplate (tp).
    assert results["article_level"]["n_articles"] == 2
    assert (
        results["article_level"]["tp"],
        results["article_level"]["fp"],
        results["article_level"]["fn"],
    ) == (1, 0, 1)
    assert list(tmp_path.glob("*_quality.json")) and list(tmp_path.glob("*_quality.md"))


def test_score_refuses_until_both_passes_exist(tmp_path, monkeypatch):
    pieces = [{**_piece("TechCrunch AI", 0), "text": "JUNK"}]
    monkeypatch.setattr(Q, "HELDOUT_PATH", tmp_path / "heldout_set.json")
    monkeypatch.setattr(Q, "LABELS_PATH", tmp_path / "heldout_labels.json")
    monkeypatch.setattr(Q, "RESULTS_DIR", tmp_path)
    _write_heldout(Q.HELDOUT_PATH, pieces)
    Q.LABELS_PATH.write_text(
        json.dumps({"TechCrunch AI-0": {"pass1": True, "pass2": None, "final": None}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="labelling pass"):
        Q.score()


def test_score_refuses_when_a_sample_is_unlabelled(tmp_path, monkeypatch):
    pieces = [{**_piece("TechCrunch AI", 0), "text": "JUNK"}]
    monkeypatch.setattr(Q, "HELDOUT_PATH", tmp_path / "heldout_set.json")
    monkeypatch.setattr(Q, "LABELS_PATH", tmp_path / "heldout_labels.json")
    monkeypatch.setattr(Q, "RESULTS_DIR", tmp_path)
    _write_heldout(Q.HELDOUT_PATH, pieces)
    Q.LABELS_PATH.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(SystemExit, match="unlabelled"):
        Q.score()
