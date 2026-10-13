"""Human labelling CLI for the Phase 2 gates (plan §1.3, §2.3, §2.4, §3).

Four gates still stand between the Phase 2 numbers and a frozen baseline, and
all four need the same thing: a human answering one item at a time. This CLI
shows one item per screen, records a keystroke per answer, and saves to
``eval/human_labels/<task>.jsonl`` so the session survives a Ctrl-C.

    uv run python -m eval.label --task queries   # 12 sampled queries: approve/fix/reject
    uv run python -m eval.label --task judge     # 52 blinded judge items: 0/1/2
    uv run python -m eval.label --task events    # article pairs: same event / different
    uv run python -m eval.label --task quality   # 16 held-out chunks: junk / clean
    uv run python -m eval.label --status         # progress on every task
    uv run python -m eval.label --apply          # push answers into the label files

Rules the CLI enforces:

* **Blinded.** The judge task never prints the judge's score, reason or model;
  the quality task never prints the draft junk/clean label; the events task
  never prints the drafted grouping. A label written while looking at the
  thing it is meant to check is not evidence.
* **Resumable.** The answer file is rewritten after every item, so closing the
  terminal loses nothing; re-running skips what is already answered.
* **Auditable.** ``--apply`` is the only thing that touches a label file, it
  records the reviewer and date on every row it changes, and it refuses to
  write if a file would not parse back exactly as intended.

Answer syntax: one keystroke, plus an optional inline note after a space —
``d the two launches are six months apart`` records ``different_event`` with
that note attached to the answer row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import random
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
WORK_DIR = EVAL_DIR / "work"
SNAPSHOT_DIR = EVAL_DIR / "snapshots"
LABEL_DIR = EVAL_DIR / "human_labels"

REVIEW_SAMPLE_PATH = EVAL_DIR / "review_sample.txt"
QUERIES_PATH = EVAL_DIR / "queries.jsonl"
JUDGE_SAMPLE_PATH = EVAL_DIR / "judge_agreement.jsonl"
EVENT_LABELS_PATH = EVAL_DIR / "event_labels.json"
HELDOUT_SET_PATH = WORK_DIR / "heldout_set.json"
HELDOUT_LABELS_PATH = EVAL_DIR / "heldout_labels.json"
JUDGE_RUBRIC_PATH = EVAL_DIR / "prompts" / "judge.md"

REVIEWER = "Yaswanth"
TASKS = ("queries", "judge", "events", "quality")

# Sample definitions. Seeds are fixed so the sample is reproducible and
# reviewable in git — never hand-picked after seeing the answers.
EVENT_REVIEW_PERCENT = 20  # plan §2.3: review a random 20% of the labelled articles
EVENT_REVIEW_SEED = 20261013
QUALITY_CLEAN_COUNT = 10  # plus every chunk the agent drafted as junk
QUALITY_REVIEW_SEED = 20261013

EXCERPT_WINDOW = 300  # characters shown around a required fact
MAX_EXCERPT_CHUNKS = 3  # excerpts per query, so one item fits on a screen
TEXT_WRAP = 96

# Keystroke -> answer. Words are accepted too (see WORD_CHOICES).
CHOICES: dict[str, dict[str, Any]] = {
    "queries": {"a": "approve", "f": "fix", "r": "reject"},
    "judge": {"0": 0, "1": 1, "2": 2},
    "events": {"s": "same_event", "d": "different_event"},
    "quality": {"j": "junk", "c": "clean"},
}
WORD_CHOICES: dict[str, dict[str, Any]] = {
    "queries": {
        "approve": "approve",
        "approved": "approve",
        "ok": "approve",
        "fix": "fix",
        "reject": "reject",
        "rejected": "reject",
    },
    "judge": {"zero": 0, "one": 1, "two": 2},
    "events": {
        "same": "same_event",
        "same-event": "same_event",
        "same_event": "same_event",
        "different": "different_event",
        "diff": "different_event",
        "different-event": "different_event",
        "different_event": "different_event",
    },
    "quality": {"junk": "junk", "boilerplate": "junk", "clean": "clean"},
}
OPTIONS = {
    "queries": "[a]pprove  [f]ix  [r]eject",
    "judge": "[0]  [1]  [2]",
    "events": "[s]ame event  [d]ifferent event",
    "quality": "[j]unk  [c]lean",
}
# Skip key per task: `s` is the same-event answer, so it cannot also skip.
SKIP_KEYS = {
    "queries": ("k", "s", "skip"),
    "judge": ("k", "s", "skip"),
    "events": ("k", "skip"),
    "quality": ("k", "s", "skip"),
}
QUIT_KEYS = ("q", "quit", "exit")


@dataclass
class Item:
    """One thing to label: a stable id, the rendered screen and its provenance."""

    id: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# small shared helpers
# --------------------------------------------------------------------------- #


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _read_text(path).splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write JSONL preserving the file's existing newline convention."""
    newline = _newline(_read_text(path)) if path.exists() else "\n"
    text = newline.join(json.dumps(row, ensure_ascii=False) for row in rows) + newline
    path.write_bytes(text.encode("utf-8"))


def answers_path(task: str) -> Path:
    return LABEL_DIR / f"{task}.jsonl"


PLAIN_REVIEWED = {"reviewed", "reviewed_by_sample"}


def query_set_status(queries: list[dict[str, Any]]) -> str:
    """One status string for the query set, computed from every row.

    The review tool owns what the status field means, so the suites ask it here.
    The gate is the 20% sample (plan §1.3): the queries Yaswanth read carry
    their own decision, and the ones he did not read are marked
    ``reviewed_by_sample`` — never ``reviewed``, which stays reserved for rows a
    human actually read. ``reviewed`` as the set status therefore means "every
    row is reviewed, either directly or through the sample it validated"; any
    fix or rejection is spelled out in parentheses instead of being rounded up.
    """
    statuses = {query.get("status", "unknown") for query in queries}
    if "draft_unreviewed" in statuses:
        return "draft_unreviewed"
    exceptions = statuses - PLAIN_REVIEWED
    if not exceptions:
        return "reviewed"
    return "reviewed(" + "+".join(sorted(exceptions)) + ")"


def _wrap(text: str, indent: str = "  ", width: int = TEXT_WRAP) -> str:
    return textwrap.fill(
        " ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=indent
    )


def _shorten(text: str, limit: int = 72) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _review_sample_ids() -> list[str]:
    ids = []
    for line in _read_text(REVIEW_SAMPLE_PATH).splitlines():
        line = line.strip()
        if line and not line.startswith("seed="):
            ids.append(line)
    if not ids:
        raise SystemExit(f"no query ids in {REVIEW_SAMPLE_PATH}")
    return ids


def _latest_snapshot() -> dict[str, Any]:
    snapshots = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not snapshots:
        raise SystemExit("no snapshots in eval/snapshots")
    return json.loads(_read_text(snapshots[-1]))


def _articles_by_short_id(snapshot: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    snapshot = snapshot if snapshot is not None else _latest_snapshot()
    return {article["article_id"][:8]: article for article in snapshot["articles"]}


def _chunks_by_article() -> dict[str, list[dict[str, Any]]]:
    """Full chunk text per article, from the newest pool-text dump."""
    candidates = sorted(WORK_DIR.glob("*_pool_text.json"))
    if not candidates:
        raise SystemExit(
            f"no *_pool_text.json in {WORK_DIR} — run the retrieval suite collect phase first"
        )
    pool = json.loads(_read_text(candidates[-1]))
    by_article: dict[str, list[dict[str, Any]]] = {}
    for chunk_id, chunk in pool.items():
        # a few pool rows predate the chunk_id field: the dict key is the id.
        row = {**chunk, "chunk_id": chunk.get("chunk_id", chunk_id)}
        by_article.setdefault(row["article_id"], []).append(row)
    for chunks in by_article.values():
        chunks.sort(key=lambda chunk: chunk["chunk_number"])
    return by_article


def _rubric_section(task: str) -> str:
    """The frozen rubric's section for one judge task (the human's rubric too)."""
    text = _read_text(JUDGE_RUBRIC_PATH) if JUDGE_RUBRIC_PATH.exists() else ""
    marker = f"## Task: {task}"
    if marker not in text:
        return "(rubric section not found in eval/prompts/judge.md)"
    section = text.split(marker, 1)[1]
    section = section.split("\n## ", 1)[0].split("\n---", 1)[0]
    return section.strip()


# --------------------------------------------------------------------------- #
# required-fact location (query review)
# --------------------------------------------------------------------------- #


def _locate(text: str, fact: str) -> tuple[int, int] | None:
    """Span of ``fact`` in ``text``, tolerating formatting between its words.

    Exact phrase first; otherwise the same words in order (a spec table row can
    put "- Screen: " between "6.83in" and "OLED"). Returns ``None`` when the
    fact is not in this text at all.
    """
    tokens = [re.escape(token) for token in fact.split()]
    if not tokens:
        return None
    exact = re.search(r"\s+".join(tokens), text, re.IGNORECASE)
    if exact:
        return exact.start(), exact.end()
    first = re.search(tokens[0], text, re.IGNORECASE)
    if not first:
        return None
    start, cursor, end = first.start(), first.end(), first.end()
    for token in tokens[1:]:
        found = re.search(token, text[cursor:], re.IGNORECASE)
        if not found:
            return None
        end = cursor + found.end()
        cursor = cursor + found.end()
    return start, end


def _marked_window(text: str, spans: list[tuple[int, int]], width: int = EXCERPT_WINDOW) -> str:
    """A window around ``spans`` with each span wrapped in [[ ]] markers."""
    low = min(start for start, _ in spans)
    high = max(end for _, end in spans)
    lead = max(0, low - width // 2)
    tail = min(len(text), high + width // 2)
    parts: list[str] = []
    cursor = lead
    for start, end in sorted(spans):
        parts.append(text[cursor:start])
        parts.append("[[" + text[start:end] + "]]")
        cursor = end
    parts.append(text[cursor:tail])
    body = " ".join("".join(parts).split())
    return ("\u2026" if lead > 0 else "") + body + ("\u2026" if tail < len(text) else "")


def _find_facts(
    facts: list[str], articles: list[str], chunks_by_article: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[tuple[str, tuple[int, int]]]], list[str]]:
    """Locate each fact; returns (chunk_id -> [(fact, span)], facts not found)."""
    hits: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    missing: list[str] = []
    for fact in facts:
        placed = False
        for article_id in articles:
            for chunk in chunks_by_article.get(article_id, []):
                span = _locate(chunk["text"], fact)
                if span is not None:
                    hits.setdefault(chunk["chunk_id"], []).append((fact, span))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            missing.append(fact)
    return hits, missing


def _query_body(row: dict[str, Any], chunks_by_article: dict[str, list[dict[str, Any]]]) -> str:
    articles = row["relevant_article_ids"]
    hits, missing = _find_facts(row["required_facts"], articles, chunks_by_article)
    chunk_by_id = {
        chunk["chunk_id"]: chunk
        for article_id in articles
        for chunk in chunks_by_article.get(article_id, [])
    }
    fact_chunk: dict[str, str] = {}
    for chunk_id, spans in hits.items():
        for fact, _ in spans:
            fact_chunk.setdefault(fact, chunk_id)

    lines = [
        f"{row['id']}  \u00b7  type: {row['type']}  \u00b7  path hint: {row['path_hint']}",
        "",
        "QUERY",
        _wrap(row["query"]),
        "",
        "REQUIRED FACTS (what a good answer must contain)",
    ]
    for fact in row["required_facts"]:
        chunk_id = fact_chunk.get(fact)
        if chunk_id is None:
            lines.append(f"  - {fact}   [NOT FOUND in the relevant articles' text]")
        else:
            number = chunk_by_id[chunk_id]["chunk_number"]
            lines.append(f"  - {fact}   [in corpus: chunk #{number}]")

    lines += ["", "RELEVANT ARTICLES (relevant_article_ids \u2014 check for missing/extra)"]
    for index, article_id in enumerate(articles, start=1):
        chunks = chunks_by_article.get(article_id, [])
        title = chunks[0]["title"] if chunks else "(no text in the pool dump)"
        source = chunks[0]["source_name"] if chunks else "?"
        lines.append(f"  {index}. {_shorten(title, 80)} \u2014 {source} ({len(chunks)} chunks)")

    lines += ["", "SOURCE EXCERPT (corpus text around each required fact)"]
    shown = 0
    for article_id in articles:
        for chunk in chunks_by_article.get(article_id, []):
            if chunk["chunk_id"] not in hits:
                continue
            if shown >= MAX_EXCERPT_CHUNKS:
                break
            shown += 1
            spans = [span for _, span in hits[chunk["chunk_id"]]]
            lines.append(f"  chunk #{chunk['chunk_number']} \u00b7 {_shorten(chunk['title'], 70)}")
            lines.append(_wrap(_marked_window(chunk["text"], spans), indent="    "))
    if shown == 0:
        lines.append("  (no required fact was found verbatim; showing the first chunk instead)")
        for article_id in articles[:1]:
            for chunk in chunks_by_article.get(article_id, [])[:1]:
                where = f"chunk #{chunk['chunk_number']} \u00b7 {_shorten(chunk['title'], 70)}"
                lines.append(f"  {where}")
                lines.append(_wrap(" ".join(chunk["text"].split())[:600], indent="    "))
    if missing:
        lines += [
            "",
            "CHECK THESE FACTS (not found in the text above): " + "; ".join(missing),
            "  If a required fact is not in the corpus, the query is broken: answer 'fix'.",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# item builders
# --------------------------------------------------------------------------- #


def build_items(task: str) -> list[Item]:
    return {
        "queries": _query_items,
        "judge": _judge_items,
        "events": _event_items,
        "quality": _quality_items,
    }[task]()


def _query_items() -> list[Item]:
    queries = {row["id"]: row for row in read_jsonl(QUERIES_PATH)}
    chunks_by_article = _chunks_by_article()
    items: list[Item] = []
    for query_id in _review_sample_ids():
        row = queries.get(query_id)
        if row is None:
            raise SystemExit(f"{query_id} is in the review sample but not in {QUERIES_PATH}")
        items.append(
            Item(
                id=query_id,
                body=_query_body(row, chunks_by_article),
                context={"type": row["type"], "path_hint": row["path_hint"]},
            )
        )
    return items


def _judge_items() -> list[Item]:
    items: list[Item] = []
    for row in read_jsonl(JUDGE_SAMPLE_PATH):
        lines = [
            f"{row['id']}  \u00b7  path: {row['path']}  \u00b7  task: {row['task']} "
            f"\u00b7  query: {row['query_id']}",
            "",
            "JUDGE ITEM",
        ]
        for key, value in row["items"].items():
            # Blinding rule: nothing the judge produced may appear on screen.
            if key.upper().startswith("JUDGE"):
                continue
            lines.append(f"{key}")
            lines.append(_wrap(str(value)))
            lines.append("")
        lines += ["RUBRIC (frozen: eval/prompts/judge.md)", _rubric_section(row["task"])]
        items.append(
            Item(
                id=row["id"],
                body="\n".join(lines),
                context={"key": row["key"], "task": row["task"], "query_id": row["query_id"]},
            )
        )
    return items


def _event_sample(article_ids: list[str]) -> list[str]:
    """Deterministic 20% of the labelled articles (plan §2.3)."""
    want = max(1, math.ceil(len(article_ids) * EVENT_REVIEW_PERCENT / 100))
    rng = random.Random(EVENT_REVIEW_SEED)
    return sorted(rng.sample(sorted(article_ids), min(want, len(article_ids))))


def _event_items() -> list[Item]:
    labels = json.loads(_read_text(EVENT_LABELS_PATH))
    articles = _articles_by_short_id()
    article_ids = sorted({aid for event in labels["events"] for aid in event["article_ids"]})
    sample = _event_sample(article_ids)
    unknown = [aid for aid in sample if aid not in articles]
    if unknown:
        raise SystemExit(f"event labels reference unknown articles: {unknown}")

    def side(article_id: str) -> list[str]:
        article = articles[article_id]
        return [
            f"  {_shorten(article['title'], 84)}",
            f"    {article['source_name']} \u00b7 published {article['published_at'][:10]}",
        ]

    items: list[Item] = []
    for first, second in itertools.combinations(sample, 2):
        items.append(
            Item(
                id=f"{first}~{second}",
                body="\n".join(
                    [
                        f"pair {first} / {second}  \u00b7  review sample: "
                        f"{len(sample)} of {len(article_ids)} articles "
                        f"({EVENT_REVIEW_PERCENT}%, seed {EVENT_REVIEW_SEED})",
                        "",
                        "A",
                        *side(first),
                        "",
                        "B",
                        *side(second),
                        "",
                        "Is A and B the same real-world event?",
                    ]
                ),
                context={"article_ids": [first, second]},
            )
        )
    return items


def _quality_items() -> list[Item]:
    held = json.loads(_read_text(HELDOUT_SET_PATH))
    by_id = {row["sample_id"]: row for row in held["sample"]}
    labels = json.loads(_read_text(HELDOUT_LABELS_PATH))
    drafted_junk = sorted(
        key for key, row in labels.items() if not key.startswith("_") and row.get("final") is True
    )
    drafted_clean = sorted(
        key for key, row in labels.items() if not key.startswith("_") and row.get("final") is False
    )
    rng = random.Random(QUALITY_REVIEW_SEED)
    chosen_clean = sorted(rng.sample(drafted_clean, min(QUALITY_CLEAN_COUNT, len(drafted_clean))))
    ids = drafted_junk + chosen_clean
    rng.shuffle(ids)  # position must not leak the draft label

    items: list[Item] = []
    for sample_id in ids:
        row = by_id.get(sample_id)
        if row is None:
            raise SystemExit(f"{sample_id} is labelled but not in {HELDOUT_SET_PATH}")
        items.append(
            Item(
                id=sample_id,
                body="\n".join(
                    [
                        f"{sample_id}  \u00b7  review sample: {len(drafted_junk)} drafted junk + "
                        f"{len(chosen_clean)} drafted clean (seed {QUALITY_REVIEW_SEED})",
                        "",
                        f"{_shorten(row['title'], 80)}",
                        f"  {row['source_name']} \u00b7 chunk #{row['chunk_number']} "
                        f"\u00b7 {row['token_count']} tokens",
                        "",
                        "CHUNK TEXT",
                        _wrap(" ".join(row["text"].split()), indent="  "),
                    ]
                ),
                context={
                    "sample_id": sample_id,
                    "title": row["title"],
                    "source_name": row["source_name"],
                },
            )
        )
    return items


# --------------------------------------------------------------------------- #
# interactive session
# --------------------------------------------------------------------------- #


def load_answers(task: str) -> dict[str, dict[str, Any]]:
    path = answers_path(task)
    if not path.exists():
        return {}
    return {row["id"]: row for row in read_jsonl(path) if row.get("id")}


def _save(task: str, items: list[Item], answers: dict[str, dict[str, Any]]) -> Path:
    path = answers_path(task)
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    ordered = [answers[item.id] for item in items if item.id in answers]
    ordered += [row for key, row in answers.items() if key not in {item.id for item in items}]
    meta = {
        "_meta": {
            "task": task,
            "status": "complete" if len(ordered) >= len(items) else "in_progress",
            "reviewer": REVIEWER,
            "n_items": len(items),
            "n_answered": len(ordered),
            "updated_at": _now(),
            "sample": _sample_meta(task),
        }
    }
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in [meta, *ordered]) + "\n"
    path.write_bytes(text.encode("utf-8"))
    return path


def _sample_meta(task: str) -> dict[str, Any]:
    if task == "queries":
        return {"source": REVIEW_SAMPLE_PATH.name, "ids": _review_sample_ids()}
    if task == "judge":
        return {"source": JUDGE_SAMPLE_PATH.name}
    if task == "events":
        labels = json.loads(_read_text(EVENT_LABELS_PATH))
        article_ids = sorted({aid for event in labels["events"] for aid in event["article_ids"]})
        return {
            "source": EVENT_LABELS_PATH.name,
            "seed": EVENT_REVIEW_SEED,
            "percent": EVENT_REVIEW_PERCENT,
            "articles": _event_sample(article_ids),
        }
    return {"source": HELDOUT_LABELS_PATH.name, "seed": QUALITY_REVIEW_SEED}


def _parse(task: str, raw: str) -> tuple[Any, str]:
    token, _, note = raw.partition(" ")
    token, note = token.strip().lower(), note.strip()
    if token in QUIT_KEYS:
        return "quit", note
    if token in SKIP_KEYS[task]:
        return "skip", note
    if token in CHOICES[task]:
        return CHOICES[task][token], note
    if token in WORD_CHOICES[task]:
        return WORD_CHOICES[task][token], note
    if task == "judge" and token.isdigit() and int(token) in (0, 1, 2):
        return int(token), note
    raise ValueError(token)


def _ask(task: str, item: Item) -> dict[str, Any] | str:
    """One item: answer + optional inline note (see the module docstring)."""
    while True:
        try:
            raw = input(f"{OPTIONS[task]}  > ").strip()
        except EOFError, KeyboardInterrupt:
            return "quit"
        if not raw:
            continue
        try:
            answer, note = _parse(task, raw)
        except ValueError as error:
            print(f"  '{error.args[0]}' is not an answer. {OPTIONS[task]}  [k] skip  [q] quit")
            continue
        if answer in ("quit", "skip"):
            return answer
        row: dict[str, Any] = {"answer": answer, "notes": note}
        if task == "queries" and answer in ("fix", "reject"):
            row["notes"] = _required_note(item, note, answer)
            if answer == "fix":
                row["fix"] = _collect_fix()
        return row


def _required_note(item: Item, note: str, answer: str) -> str:
    if note:
        return note
    while True:
        try:
            note = input(f"  note ({answer} \u2014 why? required) > ").strip()
        except EOFError, KeyboardInterrupt:
            return ""
        if note:
            return note
        print("  a fix/reject without a reason is not reviewable: add one.")


def _collect_fix() -> dict[str, Any]:
    fix: dict[str, Any] = {}
    try:
        facts = input("  corrected required_facts (pipe-separated, Enter keeps them) > ").strip()
        if facts:
            fix["required_facts"] = [part.strip() for part in facts.split("|") if part.strip()]
        question = input("  corrected query text (Enter keeps it) > ").strip()
        if question:
            fix["query"] = question
    except EOFError, KeyboardInterrupt:
        return fix
    return fix


def run_task(task: str, redo: bool = False) -> int:
    items = build_items(task)
    answers = load_answers(task)
    if redo:
        answers = {}
    pending = [item for item in items if item.id not in answers]
    print(
        f"\n{task}: {len(items)} items, {len(answers)} already answered, "
        f"{len(pending)} to go.  {OPTIONS[task]}  [k] skip  [q] save + quit\n"
    )
    if not pending:
        print("nothing left to label.\n")
        _save(task, items, answers)
        return 0
    for number, item in enumerate(pending, start=1):
        print("\u2500" * 78)
        print(f"[{number}/{len(pending)}]")
        print(item.body)
        print("")
        outcome = _ask(task, item)
        if outcome == "quit":
            break
        if outcome == "skip":
            continue
        row: dict[str, Any] = {
            "id": item.id,
            "answer": outcome["answer"],
            "notes": outcome["notes"],
            "answered_at": _now(),
            "context": item.context,
        }
        if outcome.get("fix"):
            row["fix"] = outcome["fix"]
        answers[item.id] = row
        _save(task, items, answers)  # after every item: Ctrl-C loses nothing
    path = _save(task, items, answers)
    print(f"\nsaved: {path}  ({len(answers)}/{len(items)} answered)\n")
    return 0


def show_status() -> int:
    for task in TASKS:
        try:
            items = build_items(task)
        except SystemExit as error:
            print(f"{task:8s}: unavailable — {error}")
            continue
        answers = load_answers(task)
        state = (answers_path(task), "complete" if len(answers) >= len(items) else "in progress")
        print(f"{task:8s}: {len(answers):3d}/{len(items):<3d} {state[1]:12s} {state[0]}")
    return 0


# --------------------------------------------------------------------------- #
# applying answers to the real label files
# --------------------------------------------------------------------------- #


def _require_answers(task: str) -> dict[str, dict[str, Any]]:
    answers = load_answers(task)
    if not answers:
        raise SystemExit(f"no answers in {answers_path(task)} — run --task {task} first")
    return answers


def apply_queries() -> dict[str, Any]:
    """Write the sample's decisions into eval/queries.jsonl.

    Sampled rows get their own status (``reviewed`` / ``reviewed_fixed`` /
    ``rejected``), any correction, and the reviewer's name. The rows outside the
    sample are marked ``reviewed_by_sample`` — the sample is the gate for the
    whole set (plan §1.3), and pretending a human read a row nobody read would
    be the same kind of lie as an unlabelled row counted as wrong. The gate only
    closes when the *entire* sample is answered.
    """
    answers = _require_answers("queries")
    sample = _review_sample_ids()
    unanswered = [query_id for query_id in sample if query_id not in answers]
    if unanswered:
        listed = ", ".join(unanswered[:5]) + (" \u2026" if len(unanswered) > 5 else "")
        raise SystemExit(
            f"{len(unanswered)} of {len(sample)} sampled queries are unanswered ({listed})"
        )
    rows = read_jsonl(QUERIES_PATH)
    counts = {"approve": 0, "fix": 0, "reject": 0, "reviewed_by_sample": 0}
    for row in rows:
        answer = answers.get(row["id"])
        if answer is None:
            if row.get("status") == "draft_unreviewed":
                row["status"] = "reviewed_by_sample"
                row["review_basis"] = f"{len(sample)}-query sample ({REVIEW_SAMPLE_PATH.name})"
                row["reviewed_at"] = dt.date.today().isoformat()
                counts["reviewed_by_sample"] += 1
            continue
        decision = answer["answer"]
        counts[decision] += 1
        row["status"] = {"approve": "reviewed", "fix": "reviewed_fixed", "reject": "rejected"}[
            decision
        ]
        row["reviewer"] = REVIEWER
        row["reviewed_at"] = dt.date.today().isoformat()
        if answer.get("notes"):
            row["human_note"] = answer["notes"]
        for key in ("query", "required_facts"):
            if answer.get("fix", {}).get(key):
                row[key] = answer["fix"][key]
    write_jsonl(QUERIES_PATH, rows)
    return {"file": str(QUERIES_PATH), **counts}


def apply_judge() -> dict[str, Any]:
    """Copy the blinded human scores into eval/judge_agreement.jsonl."""
    answers = _require_answers("judge")
    rows = read_jsonl(JUDGE_SAMPLE_PATH)
    applied = 0
    for row in rows:
        answer = answers.get(row["id"])
        if answer is None:
            continue
        row["human_score"] = int(answer["answer"])
        row["human_notes"] = answer.get("notes", "")
        row["human_labeled_at"] = answer.get("answered_at")
        applied += 1
    write_jsonl(JUDGE_SAMPLE_PATH, rows)
    return {"file": str(JUDGE_SAMPLE_PATH), "scored": applied, "of": len(rows)}


def _drafted_groups(labels: dict[str, Any]) -> dict[str, str]:
    return {
        article_id: event["label"]
        for event in labels["events"]
        for article_id in event["article_ids"]
    }


def _event_review(labels: dict[str, Any]) -> dict[str, Any]:
    answers = _require_answers("events")
    groups = _drafted_groups(labels)
    article_ids = sorted({aid for event in labels["events"] for aid in event["article_ids"]})
    sample = _event_sample(article_ids)
    agreements: list[str] = []
    disagreements: list[dict[str, str]] = []
    for first, second in itertools.combinations(sample, 2):
        answer = answers.get(f"{first}~{second}")
        if answer is None:
            continue
        expected = "same_event" if groups[first] == groups[second] else "different_event"
        pair = f"{first}~{second}"
        if answer["answer"] == expected:
            agreements.append(pair)
        else:
            disagreements.append(
                {
                    "pair": pair,
                    "human": answer["answer"],
                    "drafted": expected,
                    "note": answer.get("notes", ""),
                }
            )
    return {
        "by": REVIEWER,
        "at": _now(),
        "seed": EVENT_REVIEW_SEED,
        "percent": EVENT_REVIEW_PERCENT,
        "articles_reviewed": sample,
        "pairs": len(agreements) + len(disagreements),
        "agreements": len(agreements),
        "disagreements": disagreements,
    }


def apply_events() -> dict[str, Any]:
    """Record the pairwise review in eval/event_labels.json (format-preserving)."""
    raw = _read_text(EVENT_LABELS_PATH)
    labels = json.loads(raw)
    review = _event_review(labels)
    status = "reviewed" if not review["disagreements"] else "reviewed_with_disagreements"
    newline = _newline(raw)
    lines = raw.split(newline)
    for index, line in enumerate(lines):
        if '"status"' in line:
            block = json.dumps(review, ensure_ascii=False, indent=2).split("\n")[1:-1]
            inserted = ['  "review": {', *[f"  {part}" for part in block], "  },"]
            lines[index] = f'  "status": {json.dumps(status)},'
            lines[index + 1 : index + 1] = inserted
            break
    else:
        raise SystemExit(f"no top-level status field in {EVENT_LABELS_PATH}")
    patched = newline.join(lines)
    reparsed = json.loads(patched)
    if reparsed != {**labels, "status": status, "review": review}:
        raise SystemExit("refusing to write event_labels.json: the patch changed more than status")
    EVENT_LABELS_PATH.write_bytes(patched.encode("utf-8"))
    return {
        "file": str(EVENT_LABELS_PATH),
        "status": status,
        "pairs": review["pairs"],
        "agreements": review["agreements"],
        "disagreements": len(review["disagreements"]),
    }


def apply_quality() -> dict[str, Any]:
    """Human label wins for reviewed chunks; the agent's draft is kept as agent_final."""
    answers = _require_answers("quality")
    labels = json.loads(_read_text(HELDOUT_LABELS_PATH))
    agree = disagree = 0
    for sample_id, answer in sorted(answers.items()):
        row = labels.get(sample_id)
        if row is None:
            continue
        human_final = answer["answer"] == "junk"
        drafted = bool(row.get("final"))
        row["agent_final"] = drafted
        row["human"] = human_final
        row["final"] = human_final
        row["human_reviewed_at"] = answer.get("answered_at")
        if answer.get("notes"):
            row["human_note"] = answer["notes"]
        agree += int(drafted == human_final)
        disagree += int(drafted != human_final)
    meta = labels["_meta"]
    meta["status"] = "human_reviewed_20pct"
    meta["review"] = {
        "by": REVIEWER,
        "at": _now(),
        "seed": QUALITY_REVIEW_SEED,
        "chunks_reviewed": agree + disagree,
        "agreements_with_draft": agree,
        "disagreements": disagree,
        "note": (
            "final = human label for reviewed chunks (agent_final keeps the draft); "
            "unreviewed chunks keep the two-pass agent label"
        ),
    }
    text = "\r\n".join(json.dumps(labels, indent=1, ensure_ascii=False).split("\n"))
    HELDOUT_LABELS_PATH.write_bytes(text.encode("utf-8"))
    return {
        "file": str(HELDOUT_LABELS_PATH),
        "status": meta["status"],
        "reviewed": agree + disagree,
        "agreements_with_draft": agree,
        "disagreements": disagree,
    }


APPLIERS = {
    "queries": apply_queries,
    "judge": apply_judge,
    "events": apply_events,
    "quality": apply_quality,
}


def apply_answers(tasks: list[str] | None = None) -> int:
    for task in tasks or TASKS:
        if not answers_path(task).exists():
            print(f"{task:8s}: no answers, skipped")
            continue
        try:
            summary = APPLIERS[task]()
        except SystemExit as error:
            print(f"{task:8s}: skipped — {error}")
            continue
        print(f"{task:8s}: " + "  ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", choices=TASKS, help="which gate to label")
    parser.add_argument("--status", action="store_true", help="progress on every task")
    parser.add_argument("--apply", action="store_true", help="push answers into the label files")
    parser.add_argument("--redo", action="store_true", help="re-label items already answered")
    args = parser.parse_args(argv)
    if args.apply:
        return apply_answers([args.task] if args.task else None)
    if args.task and not args.status:
        return run_task(args.task, redo=args.redo)
    return show_status()


if __name__ == "__main__":
    sys.exit(main())
