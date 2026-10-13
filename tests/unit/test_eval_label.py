"""Unit tests for eval.label — the human-gate CLI's items, saves and apply steps.

The CLI is where four measurement gates are decided, so the contracts that
matter are tested here: the sample is deterministic, the judge/quality/event
screens never show what they are meant to check, answers survive a quit, and
``--apply`` changes only the fields it claims to.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from eval import label as LAB

CRLF = "\r\n"


def _write_jsonl(path: Path, rows: list[dict], newline: str = CRLF) -> None:
    path.write_bytes(
        (newline.join(json.dumps(row, ensure_ascii=False) for row in rows) + newline).encode(
            "utf-8"
        )
    )


def _write_json(path: Path, payload: dict, indent: int = 2, newline: str = "\n") -> None:
    body = newline.join(json.dumps(payload, indent=indent, ensure_ascii=False).split("\n"))
    path.write_bytes(body.encode("utf-8"))


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def work(tmp_path, monkeypatch):
    """Point every file the CLI reads at a miniature, self-consistent corpus."""
    pool = {
        "c-1": {
            "chunk_id": "c-1",
            "article_id": "art-alpha",
            "title": "Widget review: the aluminium one",
            "source_name": "The Guardian Technology",
            "chunk_number": 0,
            "text": "The Widget has a huge 6.83in OLED screen and costs from GBP 499.",
        },
        "c-2": {
            # a pool row without chunk_id: the dict key is the id
            "article_id": "art-alpha",
            "title": "Widget review: the aluminium one",
            "source_name": "The Guardian Technology",
            "chunk_number": 1,
            "text": "Battery life reached 40% smaller after the firmware update.",
        },
        "c-3": {
            "chunk_id": "c-3",
            "article_id": "art-beta",
            "title": "What the EU AI Act means for models",
            "source_name": "BBC Science & Tech",
            "chunk_number": 0,
            "text": "The EU AI Act entered force in December.",
        },
    }
    pool_path = tmp_path / "work" / "20260101_snap_pool_text.json"
    pool_path.parent.mkdir(parents=True)
    pool_path.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")

    queries = [
        {
            "id": "q001",
            "snapshot": "snap",
            "query": "What size screen does the Widget have?",
            "type": "fact",
            "language": "en",
            "path_hint": "fast",
            "relevant_article_ids": ["art-alpha"],
            "relevant_chunk_ids": ["c-1", "c-2"],
            "required_facts": ["6.83in OLED"],
            "relevance_labels": {},
            "status": "draft_unreviewed",
            "labeler": "coding-agent",
            "drafted_at": "2026-01-01",
        },
        {
            "id": "q002",
            "snapshot": "snap",
            "query": "Which regulation landed in December?",
            "type": "fact",
            "language": "en",
            "path_hint": "fast",
            "relevant_article_ids": ["art-beta"],
            "relevant_chunk_ids": ["c-3"],
            "required_facts": ["EU AI Act"],
            "relevance_labels": {},
            "status": "draft_unreviewed",
            "labeler": "coding-agent",
            "drafted_at": "2026-01-01",
        },
    ]
    queries_path = tmp_path / "queries.jsonl"
    _write_jsonl(queries_path, queries)

    review_sample = tmp_path / "review_sample.txt"
    review_sample.write_bytes(b"seed=1 sample_size=1\r\nq001\r\n")

    judge_sample = tmp_path / "judge_agreement.jsonl"
    _write_jsonl(
        judge_sample,
        [
            {
                "id": "j000",
                "key": "q001|deep|sub_question_relevance|0",
                "query_id": "q001",
                "path": "deep",
                "task": "sub_question_relevance",
                "items": {
                    "USER QUERY": "What size screen does the Widget have?",
                    "SUB-QUESTION": "Does the Widget screen support 144Hz?",
                },
                "human_score": None,
                "human_notes": "",
                "judge_score": 2,
                "judge_model": "qwen2.5:7b",
                "judge_reason": "SECRET-JUDGE-REASON",
            }
        ],
    )

    snapshot = {
        "articles": [
            {
                "article_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "source_name": "TechCrunch AI",
                "title": "Anthropic explains its watermarks",
                "published_at": "2026-08-15T01:00:00+00:00",
            },
            {
                "article_id": "bbbbbbbb-0000-0000-0000-000000000002",
                "source_name": "TechCrunch AI",
                "title": "Google lets users remove AI watermarks",
                "published_at": "2026-08-14T01:00:00+00:00",
            },
            {
                "article_id": "cccccccc-0000-0000-0000-000000000003",
                "source_name": "BBC Science & Tech",
                "title": "Nvidia gets $500bn from Wall Street",
                "published_at": "2026-08-11T01:00:00+00:00",
            },
        ]
    }
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    _write_json(snapshot_dir / "snap.json", snapshot)

    event_labels = {
        "status": "agent_drafted_pending_20pct_review",
        "labeler": "coding-agent",
        "notes": "hand-written",
        "events": [
            {"label": "watermarking", "article_ids": ["aaaaaaaa", "bbbbbbbb"], "notes": ""},
            {"label": "nvidia", "article_ids": ["cccccccc"], "notes": "singleton"},
        ],
    }
    event_path = tmp_path / "event_labels.json"
    _write_json(event_path, event_labels)

    heldout_set = {
        "sample_seed": 1,
        "sample": [
            {
                "sample_id": f"art-{index}#0",
                "article_id": f"art-{index}",
                "source_name": "BBC Science & Tech" if index % 2 else "TechCrunch AI",
                "title": f"Article {index}",
                "extractor": "trafilatura",
                "extraction_quality": "ok",
                "chunk_number": 0,
                "token_count": 120,
                "text": f"Body text of chunk {index}.",
            }
            for index in range(28)
        ],
    }
    heldout_path = tmp_path / "heldout_set.json"
    heldout_path.write_text(json.dumps(heldout_set, indent=1), encoding="utf-8")

    labels = {"_meta": {"status": "agent_drafted_two_passes_pending_review", "review": "pending"}}
    for index in range(28):
        labels[f"art-{index}#0"] = {
            "pass1": index < 8,
            "pass2": index < 8,
            "final": index < 8,
            "source_name": "BBC Science & Tech" if index % 2 else "TechCrunch AI",
            "title": f"Article {index}",
            "note": "",
        }
    labels_path = tmp_path / "heldout_labels.json"
    _write_json(labels_path, labels, indent=1, newline=CRLF)

    rubric = tmp_path / "prompts"
    rubric.mkdir()
    (rubric / "judge.md").write_text(
        "# rubric\n\n## Task: sub_question_relevance\n\n- 2 - directly targets\n- 0 - unrelated\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(LAB, "WORK_DIR", pool_path.parent)
    monkeypatch.setattr(LAB, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(LAB, "LABEL_DIR", tmp_path / "human_labels")
    monkeypatch.setattr(LAB, "QUERIES_PATH", queries_path)
    monkeypatch.setattr(LAB, "REVIEW_SAMPLE_PATH", review_sample)
    monkeypatch.setattr(LAB, "JUDGE_SAMPLE_PATH", judge_sample)
    monkeypatch.setattr(LAB, "EVENT_LABELS_PATH", event_path)
    monkeypatch.setattr(LAB, "HELDOUT_SET_PATH", heldout_path)
    monkeypatch.setattr(LAB, "HELDOUT_LABELS_PATH", labels_path)
    monkeypatch.setattr(LAB, "JUDGE_RUBRIC_PATH", rubric / "judge.md")
    return tmp_path


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #


def test_query_item_shows_the_fact_location_and_marks_the_excerpt(work):
    items = LAB.build_items("queries")
    assert [item.id for item in items] == ["q001"]
    body = items[0].body
    assert "What size screen does the Widget have?" in body
    assert "[in corpus: chunk #0]" in body
    assert "[[6.83in OLED]]" in body  # the fact is bracketed inside its own text
    assert "Widget review: the aluminium one" in body


def test_query_item_flags_a_fact_the_corpus_does_not_contain(work, monkeypatch):
    monkeypatch.setattr(LAB, "_review_sample_ids", lambda: ["q002"])
    rows = LAB.read_jsonl(LAB.QUERIES_PATH)
    rows[1]["required_facts"] = ["a nonsense fact"]
    _write_jsonl(LAB.QUERIES_PATH, rows)
    body = LAB.build_items("queries")[0].body
    assert "[NOT FOUND in the relevant articles' text]" in body
    assert "answer 'fix'" in body


def test_judge_item_is_blinded_and_carries_the_frozen_rubric(work):
    body = LAB.build_items("judge")[0].body
    assert "Does the Widget screen support 144Hz?" in body
    assert "directly targets" in body  # the rubric the human scores with
    assert "SECRET-JUDGE-REASON" not in body
    assert "qwen2.5:7b" not in body
    assert "judge_score" not in body


def test_quality_sample_is_every_drafted_junk_plus_ten_clean_and_deterministic(work):
    items = LAB.build_items("quality")
    assert len(items) == 18  # 8 drafted junk + 10 clean
    ids = [item.id for item in items]
    assert len(set(ids)) == 18
    drafted_junk = {f"art-{index}#0" for index in range(8)}
    assert drafted_junk <= set(ids)
    assert [item.id for item in LAB.build_items("quality")] == ids  # same sample every run
    assert "Body text of chunk" in items[0].body


def test_event_sample_is_twenty_percent_and_pairs_are_unique(work):
    items = LAB.build_items("events")
    # 20% of 3 articles -> 1 article, and a single article cannot be paired
    assert items == []
    monkeypatch_ids = [f"{index:08d}" for index in range(20)]
    sample = LAB._event_sample(monkeypatch_ids)
    assert len(sample) == 4  # ceil(20% of 20)
    assert sample == LAB._event_sample(monkeypatch_ids)  # deterministic


def test_event_items_show_both_titles_and_never_the_drafted_grouping(work, monkeypatch):
    monkeypatch.setattr(LAB, "EVENT_REVIEW_PERCENT", 100)
    items = LAB.build_items("events")
    assert len(items) == 3  # 3 choose 2
    body = "\n".join(item.body for item in items)
    assert "Anthropic explains its watermarks" in body
    assert "Nvidia gets $500bn from Wall Street" in body
    assert "watermarking" not in body  # the drafted label stays hidden
    assert "singleton" not in body


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #


def test_run_task_saves_after_every_item_and_resumes(work, monkeypatch):
    items = [
        LAB.Item(id="a", body="item a"),
        LAB.Item(id="b", body="item b"),
        LAB.Item(id="c", body="item c"),
    ]
    monkeypatch.setattr(LAB, "build_items", lambda task: items)
    # approve a, fix b (note + corrected facts), then quit before c
    answers = iter(["a", "f too broad", "fact one | fact two", "", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))

    assert LAB.run_task("queries") == 0
    saved = LAB.read_jsonl(LAB.answers_path("queries"))
    meta, rows = saved[0]["_meta"], saved[1:]
    assert meta["status"] == "in_progress"
    assert meta["n_items"] == 3
    assert [row["id"] for row in rows] == ["a", "b"]
    assert rows[0]["answer"] == "approve"
    assert rows[1]["notes"] == "too broad"
    assert rows[1]["fix"] == {"required_facts": ["fact one", "fact two"]}

    # resuming only asks about the item that was never answered
    assert set(LAB.load_answers("queries")) == {"a", "b"}
    asked: list[str] = []
    monkeypatch.setattr(builtins, "input", lambda prompt="": asked.append(prompt) or "r")
    LAB.run_task("queries")
    assert len(asked) == 2  # the answer prompt, and the reason the reject now needs
    assert set(LAB.load_answers("queries")) == {"a", "b", "c"}
    assert LAB.load_answers("queries")["c"]["answer"] == "reject"
    saved = LAB.read_jsonl(LAB.answers_path("queries"))
    assert saved[0]["_meta"]["status"] == "complete"


def test_run_task_skips_and_accepts_word_answers(work, monkeypatch):
    items = [LAB.Item(id="a", body="x"), LAB.Item(id="b", body="y")]
    monkeypatch.setattr(LAB, "build_items", lambda task: items)
    answers = iter(["skip", "two"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    LAB.run_task("judge")
    stored = LAB.load_answers("judge")
    assert set(stored) == {"b"}
    assert stored["b"]["answer"] == 2  # an int, as the kappa math expects


def test_status_reports_progress_without_touching_a_label_file(work, capsys):
    assert LAB.show_status() == 0
    out = capsys.readouterr().out
    assert "queries" in out and "0/1" in out
    assert not LAB.answers_path("queries").exists()


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #


def _answer_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(path, [{"_meta": {"task": "t"}}, *rows], newline="\n")


def test_apply_queries_sets_status_notes_and_corrections(work):
    _answer_rows(
        LAB.answers_path("queries"),
        [
            {
                "id": "q001",
                "answer": "fix",
                "notes": "the fact should be the 144Hz refresh rate",
                "answered_at": "2026-10-13T00:00:00+00:00",
                "context": {},
                "fix": {"required_facts": ["6.83in 144Hz OLED"], "query": "What screen?"},
            }
        ],
    )
    summary = LAB.apply_queries()
    assert summary["fix"] == 1
    assert summary["reviewed_by_sample"] == 1  # q002 is outside the sample
    rows = LAB.read_jsonl(LAB.QUERIES_PATH)
    assert rows[0]["status"] == "reviewed_fixed"
    assert rows[0]["required_facts"] == ["6.83in 144Hz OLED"]
    assert rows[0]["query"] == "What screen?"
    assert rows[0]["human_note"].startswith("the fact should be")
    assert rows[0]["reviewer"] == LAB.REVIEWER
    assert rows[1]["status"] == "reviewed_by_sample"
    assert "reviewer" not in rows[1]  # nobody read it: the sample validated it
    assert rows[1]["review_basis"].startswith("1-query sample")
    assert LAB.query_set_status(rows) == "reviewed(reviewed_fixed)"


def test_apply_queries_refuses_until_the_whole_sample_is_answered(work):
    _answer_rows(
        LAB.answers_path("queries"),
        [{"id": "q002", "answer": "reject", "notes": "wrong device"}],
    )
    with pytest.raises(SystemExit, match="unanswered"):
        LAB.apply_queries()
    # nothing was written: a half-finished review never freezes the set
    assert all(row["status"] == "draft_unreviewed" for row in LAB.read_jsonl(LAB.QUERIES_PATH))


def test_apply_queries_rejects_and_preserves_crlf(work):
    _answer_rows(
        LAB.answers_path("queries"),
        [
            {"id": "q001", "answer": "approve", "notes": ""},
            {"id": "q002", "answer": "reject", "notes": "asks about a different device"},
        ],
    )
    LAB.apply_queries()
    raw = LAB.QUERIES_PATH.read_bytes()
    assert raw.count(b"\r\n") == 2 and b"\r\r" not in raw
    rows = LAB.read_jsonl(LAB.QUERIES_PATH)
    assert rows[1]["status"] == "rejected"
    assert rows[1]["human_note"] == "asks about a different device"
    assert LAB.query_set_status(rows) == "reviewed(rejected)"


def test_apply_judge_copies_scores_into_the_committed_sample(work):
    _answer_rows(
        LAB.answers_path("judge"),
        [{"id": "j000", "answer": 1, "notes": "tangential", "answered_at": "2026-10-13T00:00:00Z"}],
    )
    summary = LAB.apply_judge()
    assert summary == {"file": str(LAB.JUDGE_SAMPLE_PATH), "scored": 1, "of": 1}
    row = LAB.read_jsonl(LAB.JUDGE_SAMPLE_PATH)[0]
    assert row["human_score"] == 1
    assert row["human_notes"] == "tangential"
    assert row["judge_score"] == 2  # the judge's own score is left for the comparison


def test_apply_events_records_agreement_and_keeps_the_events(work, monkeypatch):
    # 100% review, so common pairs (one within a drafted event, one across) are asked
    monkeypatch.setattr(LAB, "EVENT_REVIEW_PERCENT", 100)
    _answer_rows(
        LAB.answers_path("events"),
        [
            {"id": "aaaaaaaa~bbbbbbbb", "answer": "same_event", "notes": ""},
            {"id": "aaaaaaaa~cccccccc", "answer": "same_event", "notes": "actually one story"},
        ],
    )
    summary = LAB.apply_events()
    assert summary["pairs"] == 2 and summary["agreements"] == 1 and summary["disagreements"] == 1
    assert summary["status"] == "reviewed_with_disagreements"
    payload = json.loads(LAB.EVENT_LABELS_PATH.read_text(encoding="utf-8"))
    assert payload["status"] == "reviewed_with_disagreements"
    assert payload["review"]["disagreements"][0]["pair"] == "aaaaaaaa~cccccccc"
    assert payload["events"][0]["label"] == "watermarking"  # grouping untouched
    raw = LAB.EVENT_LABELS_PATH.read_text(encoding="utf-8")
    assert raw.splitlines()[1].strip() == '"status": "reviewed_with_disagreements",'
    assert '"review": {' in raw


def test_apply_events_with_agreement_is_a_clean_review(work, monkeypatch):
    monkeypatch.setattr(LAB, "EVENT_REVIEW_PERCENT", 100)
    _answer_rows(
        LAB.answers_path("events"),
        [
            {"id": "aaaaaaaa~bbbbbbbb", "answer": "same_event", "notes": ""},
            {"id": "aaaaaaaa~cccccccc", "answer": "different_event", "notes": ""},
        ],
    )
    assert LAB.apply_events()["status"] == "reviewed"


def test_apply_quality_human_label_wins_and_the_draft_is_kept(work):
    _answer_rows(
        LAB.answers_path("quality"),
        [
            {
                "id": "art-0#0",
                "answer": "clean",
                "notes": "real prose",
                "answered_at": "2026-10-13T00:00:00+00:00",
            },
            {
                "id": "art-20#0",
                "answer": "junk",
                "notes": "promo rail",
                "answered_at": "2026-10-13T00:01:00+00:00",
            },
        ],
    )
    summary = LAB.apply_quality()
    assert summary["reviewed"] == 2 and summary["disagreements"] == 2
    labels = json.loads(LAB.HELDOUT_LABELS_PATH.read_text(encoding="utf-8"))
    assert labels["art-0#0"]["final"] is False  # agent said junk, human said clean
    assert labels["art-0#0"]["agent_final"] is True
    assert labels["art-20#0"]["final"] is True  # agent said clean, human said junk
    assert labels["_meta"]["status"] == "human_reviewed_20pct"
    assert labels["_meta"]["review"]["chunks_reviewed"] == 2
    raw = LAB.HELDOUT_LABELS_PATH.read_bytes()
    assert raw.count(b"\r\n") > 50 and not raw.endswith(b"\n")  # the file's own format


def test_apply_all_skips_tasks_without_answers(work, capsys):
    assert LAB.apply_answers() == 0
    out = capsys.readouterr().out
    assert out.count("no answers, skipped") == 4
    assert json.loads(LAB.EVENT_LABELS_PATH.read_text(encoding="utf-8"))["status"].startswith(
        "agent_drafted"
    )


# --------------------------------------------------------------------------- #
# status summary
# --------------------------------------------------------------------------- #


def test_query_set_status_is_reviewed_only_once_no_row_is_still_draft():
    assert LAB.query_set_status([{"status": "reviewed"}]) == "reviewed"
    assert (
        LAB.query_set_status([{"status": "reviewed_by_sample"}, {"status": "reviewed"}])
        == "reviewed"
    )
    assert LAB.query_set_status([{"status": "reviewed"}, {"status": "rejected"}]) == (
        "reviewed(rejected)"
    )
    assert LAB.query_set_status([{"status": "reviewed"}, {}]) == "reviewed(unknown)"
    assert (
        LAB.query_set_status([{"status": "draft_unreviewed"}, {"status": "reviewed_by_sample"}])
        == "draft_unreviewed"
    )
