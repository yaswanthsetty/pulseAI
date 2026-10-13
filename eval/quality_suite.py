"""Content-quality suite: honest held-out measurement of the boilerplate filter.

The Phase 1.2 numbers (P=0.984 / R=0.969) are in-sample; this suite measures
the same classifier on **freshly ingested chunks it has never seen** (plan
§2.4): new URLs from the existing four sources only, stratified with at
>=15 TechCrunch and >=15 BBC chunks (the junk-heavy sources). Labels are
drafted by the coding agent in two passes with a cool-off (self-consistency,
not inter-annotator agreement) and reviewed by Yaswanth.

**What is sampled matters.** The pipeline filters boilerplate *before* chunks
are persisted, so the chunks that reach the database are, by construction,
chunks the classifier already accepted — scoring those would measure nothing.
The held-out set is therefore sampled from the **pre-filter pieces**: the
article text is chunked exactly as the pipeline chunks it, and every piece is
a candidate, whether or not the classifier would have kept it.

Phases:

* ``collect`` — poll each active source once (new URLs only; dedupe keeps the
  corpus honest) and synchronously process *and embed* the new articles, so
  the corpus stays consistent. The new article ids are recorded in
  ``eval/work/ingest_log.json`` and the held-out set is sampled from them.
* ``sample``  — (re)build the stratified held-out set of pre-filter pieces in
  ``eval/work/heldout_set.json`` from the recorded ingest, without touching
  the network.
* ``score``  — chunk-level precision/recall of ``is_boilerplate`` vs the
  final human labels, with 95% Wilson intervals (never hidden). Trigger for
  revisiting the threshold: held-out precision < 0.95.

The two labelling passes live in ``eval/heldout_labels.json``; the score
phase refuses to run until both passes exist for every sample.

Usage:
    uv run python -m eval.quality_suite collect
    uv run python -m eval.quality_suite score
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
WORK_DIR = EVAL_DIR / "work"
RESULTS_DIR = EVAL_DIR / "results"
HELDOUT_PATH = WORK_DIR / "heldout_set.json"
INGEST_LOG_PATH = WORK_DIR / "ingest_log.json"
LABELS_PATH = EVAL_DIR / "heldout_labels.json"

TARGET_SIZE = 50
JUNK_HEAVY_MIN = 15  # >=15 TechCrunch and >=15 BBC chunks (plan §2.4)
SAMPLE_SEED = 20261012  # deterministic sample: reviewable in git, not hand-picked
CHUNK_TEXT_CHARS = 1500  # enough to judge boilerplate; keeps the label file small


def _git_sha() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _piece_candidates(article: Any, source_name: str, chunk_text, estimate_tokens) -> list[dict]:
    """Chunk one article the way the pipeline does, keeping every piece."""
    from backend.core.config import settings
    from backend.modules.retrieval.service import _article_text  # noqa: PLC2701 - the real text

    pieces = chunk_text(
        _article_text(article),
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        single_chunk_max_tokens=settings.single_chunk_max_tokens,
    )
    return [
        {
            "sample_id": f"{article.id}#{number}",
            "article_id": str(article.id),
            "source_name": source_name,
            "title": article.title,
            "extractor": article.extractor,
            "extraction_quality": article.extraction_quality,
            "chunk_number": number,
            "token_count": estimate_tokens(piece),
            "text": piece[:CHUNK_TEXT_CHARS],
        }
        for number, piece in enumerate(pieces)
        if piece.strip()
    ]


def _is_junk_heavy(source_name: str) -> bool:
    lowered = source_name.lower()
    return "techcrunch" in lowered or "bbc" in lowered


def _stratified_sample(by_source: dict[str, list[dict]], rng: random.Random) -> list[dict]:
    """>=15 TechCrunch and >=15 BBC pieces, then fill to the target size.

    Seeded shuffle per source, so the sample is reproducible and not chosen by
    whoever ran the suite. The junk-heavy floor is per source: piling both
    sources into one list and slicing the first 50 fills the sample from
    whichever source happens to sort first, which is how a run can silently
    end up with zero TechCrunch chunks.
    """
    shuffled_by_source = {name: list(pieces) for name, pieces in by_source.items()}
    for pieces in shuffled_by_source.values():
        rng.shuffle(pieces)

    junk_heavy = sorted(name for name in shuffled_by_source if _is_junk_heavy(name))
    taken: dict[str, int] = {}
    sample: list[dict] = []
    for name in junk_heavy:
        take = min(JUNK_HEAVY_MIN, len(shuffled_by_source[name]))
        sample.extend(shuffled_by_source[name][:take])
        taken[name] = take

    leftovers = [
        piece
        for name, pieces in shuffled_by_source.items()
        for piece in pieces[taken.get(name, 0) :]
    ]
    rng.shuffle(leftovers)
    sample.extend(leftovers[: max(0, TARGET_SIZE - len(sample))])
    return sample[:TARGET_SIZE]


def collect() -> Path:
    """Poll every active source once; sample a stratified held-out chunk set."""
    from backend.core.database import SessionLocal
    from backend.db.models import Article, Source
    from backend.modules.ingestion import service as ingestion
    from backend.modules.retrieval import service as retrieval
    from sqlalchemy import select

    db = SessionLocal()
    try:
        sources = db.execute(select(Source).where(Source.status != "disabled")).scalars().all()
        if not sources:
            raise SystemExit("no active sources in the DB")
        new_ids: list[str] = []
        poll_log: list[dict[str, Any]] = []
        for source in sources:
            before = {
                str(row[0])
                for row in db.execute(
                    select(Article.id).where(Article.source_id == source.id)
                ).all()
            }
            outcome = ingestion.poll_source(db, source.id)
            after = {
                str(row[0])
                for row in db.execute(
                    select(Article.id).where(Article.source_id == source.id)
                ).all()
            }
            added = sorted(after - before)
            new_ids.extend(added)
            poll_log.append(
                {"source": source.name, "status": outcome.status, "new_articles": len(added)}
            )
            # Process + embed synchronously (fetch + extract + chunk + embed) so
            # the suite does not depend on the RQ worker, and the corpus is left
            # in the state a real poll would leave it in.
            for index, article_id in enumerate(added, start=1):
                try:
                    ingestion.process_article(db, _uuid(article_id))
                    retrieval.embed_article(db, _uuid(article_id))
                except Exception as exc:  # noqa: BLE001 - one bad article must not sink the run
                    poll_log[-1]["errors"] = poll_log[-1].get("errors", 0) + 1
                    print(f"  ingest failed for {article_id}: {exc}", flush=True)
                if index % 10 == 0:
                    print(f"  {source.name}: ingested {index}/{len(added)}", flush=True)

        if not new_ids:
            raise SystemExit(
                "no new articles ingested — feeds have no unseen URLs right now. "
                f"poll log: {poll_log}"
            )

        ingest_log = {
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "git_sha": _git_sha(),
            "poll_log": poll_log,
            "new_article_ids": new_ids,
            "n_new_articles": len(new_ids),
        }
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        INGEST_LOG_PATH.write_text(json.dumps(ingest_log, indent=1), encoding="utf-8")
        print(f"ingest log   : {INGEST_LOG_PATH} ({len(new_ids)} new articles)")
    finally:
        db.close()

    # Sampling is a separate step: the sample can be rebuilt (a rule change, a
    # bug in the stratification) without re-polling feeds that no longer have
    # unseen URLs.
    return sample_from_log()


def sample_from_log(ingest_log_path: Path = INGEST_LOG_PATH) -> Path:
    """Build the stratified held-out set from a recorded ingest."""
    from backend.core.database import SessionLocal
    from backend.db.models import Article, Source
    from backend.modules.retrieval.chunker import chunk_text, estimate_tokens
    from sqlalchemy import select

    ingest_log = json.loads(ingest_log_path.read_text(encoding="utf-8"))
    new_ids = list(ingest_log["new_article_ids"])
    if not new_ids:
        raise SystemExit(f"{ingest_log_path} records no new articles")

    db = SessionLocal()
    try:
        # Pre-filter pieces: the classifier's real input (see module docstring).
        by_source: dict[str, list[dict]] = {}
        rows = db.execute(
            select(Article, Source)
            .join(Source, Article.source_id == Source.id)
            .where(Article.id.in_([_uuid(i) for i in new_ids]))
        ).all()
        if not rows:
            raise SystemExit("no articles found for the recorded ingest ids")
        for article, source in rows:
            pieces = _piece_candidates(article, source.name, chunk_text, estimate_tokens)
            by_source.setdefault(source.name, []).extend(pieces)
    finally:
        db.close()

    sample = _stratified_sample(by_source, random.Random(SAMPLE_SEED))
    tc = sum(1 for piece in sample if "techcrunch" in piece["source_name"].lower())
    bbc = sum(1 for piece in sample if "bbc" in piece["source_name"].lower())
    if tc < JUNK_HEAVY_MIN or bbc < JUNK_HEAVY_MIN:
        raise SystemExit(
            f"stratification below target (techcrunch={tc}, bbc={bbc}); a sample that "
            "cannot honour the plan's floor is not written"
        )

    payload = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "git_sha": _git_sha(),
        "sample_seed": SAMPLE_SEED,
        "sampling_unit": "pre-filter chunks (every piece the classifier sees)",
        "ingest_log": ingest_log_path.name,
        "poll_log": ingest_log["poll_log"],
        "n_new_articles": ingest_log["n_new_articles"],
        "n_candidate_chunks": sum(len(pieces) for pieces in by_source.values()),
        "sample": sample,
        "stratification": {"techcrunch": tc, "bbc": bbc, "total": len(sample)},
    }
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    HELDOUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(
        f"held-out set : {HELDOUT_PATH} ({len(sample)} chunks of "
        f"{payload['n_candidate_chunks']} candidates; tc={tc}, bbc={bbc})"
    )
    return HELDOUT_PATH


def score() -> dict[str, Any]:
    """Chunk-level precision/recall of the classifier vs final human labels."""
    from eval.stats import precision_recall

    heldout = json.loads(HELDOUT_PATH.read_text(encoding="utf-8"))
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))

    sample = heldout["sample"]
    ids = [piece["sample_id"] for piece in sample]
    missing = [sample_id for sample_id in ids if sample_id not in labels]
    if missing:
        raise SystemExit(f"{len(missing)} chunks unlabelled — run both labelling passes first")
    incomplete = [
        sample_id
        for sample_id in ids
        if labels[sample_id].get("pass1") is None
        or labels[sample_id].get("pass2") is None
        or labels[sample_id].get("final") is None
    ]
    if incomplete:
        raise SystemExit(f"{len(incomplete)} chunks missing a labelling pass")

    # The classifier is the thing under test: import it here, not at module
    # import, so the eval package stays importable without the backend stack.
    from backend.core.content_quality import boilerplate_score, is_boilerplate

    predicted: list[bool] = []
    truth: list[bool] = []
    disagreements: list[str] = []
    for piece in sample:
        row = labels[piece["sample_id"]]
        if row["pass1"] != row["pass2"]:
            disagreements.append(piece["sample_id"])
        predicted.append(bool(is_boilerplate(piece["text"])))
        truth.append(bool(row["final"]))
        # kept for the operator: how close the raw score is to the threshold
        piece["boilerplate_score"] = boilerplate_score(piece["text"])

    metrics = precision_recall(predicted, truth)

    # Article-level view: the whole-body gate (extraction_quality=low) vs any
    # boilerplate piece in that article.
    by_article: dict[str, dict[str, Any]] = {}
    for piece, is_truth in zip(sample, truth, strict=True):
        entry = by_article.setdefault(
            piece["article_id"],
            {"predicted_low": False, "any_boilerplate": False, "n_chunks": 0},
        )
        # extraction_quality is an article column; `or` keeps the rollup stable
        # whichever piece of the article is seen first.
        entry["predicted_low"] = entry["predicted_low"] or piece["extraction_quality"] == "low"
        entry["any_boilerplate"] = entry["any_boilerplate"] or is_truth
        entry["n_chunks"] += 1
    article_rows = list(by_article.values())
    article_metrics = precision_recall(
        [row["predicted_low"] for row in article_rows],
        [row["any_boilerplate"] for row in article_rows],
    )

    results = {
        "suite": "quality",
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "heldout": {
            "file": "eval/work/heldout_set.json",
            "created_at": heldout["created_at"],
            "sampling_unit": heldout.get("sampling_unit"),
            "n_chunks": len(sample),
            "n_candidate_chunks": heldout.get("n_candidate_chunks"),
            "stratification": heldout["stratification"],
            "sample_ids": ids,
            "poll_log": heldout["poll_log"],
        },
        "labels": {
            "file": "eval/heldout_labels.json",
            "status": labels.get("_meta", {}).get("status", "unknown"),
            "self_consistency_disagreements": len(disagreements),
            "disagreement_chunks": disagreements,
            "note": "two passes by the same labeler (coding agent); this is a "
            "self-consistency check, not inter-annotator agreement",
        },
        "chunk_level": metrics,
        "article_level": {**article_metrics, "n_articles": len(article_rows)},
        "trigger": {
            "rule": "revisit BOILERPLATE_THRESHOLD if held-out precision < 0.95",
            # Undefined precision cannot clear the bar: it fires the trigger too.
            "fired": metrics["precision"] is None or metrics["precision"] < 0.95,
            "note": _trigger_note(metrics),
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    name = f"{stamp}_{results['git_sha']}_quality"
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    md = _render_md(results)
    (RESULTS_DIR / f"{name}.md").write_text(md, encoding="utf-8")
    print(md)
    return results


def _fmt_ci(ci: tuple[float, float]) -> str:
    return f"{ci[0]:.3f}-{ci[1]:.3f}"


def _fmt_precision(value: float | None) -> str:
    """Precision with no positive predictions is undefined, not zero."""
    return f"{value:.3f}" if value is not None else "undefined"


def _trigger_note(metrics: dict[str, Any]) -> str:
    if metrics["precision"] is None:
        return (
            "precision is undefined (the classifier predicted no boilerplate in this "
            f"set: {metrics['tp']} true positives, {metrics['fn']} missed) — the "
            "threshold trigger fires on recall, not on a fabricated precision"
        )
    return ""


def _render_md(results: dict[str, Any]) -> str:
    chunk = results["chunk_level"]
    article = results["article_level"]
    held = results["heldout"]
    labels = results["labels"]
    return "\n".join(
        [
            f"# Content-quality suite (held-out) — {results['git_sha']}",
            "",
            f"Held-out set: {held['n_chunks']} freshly ingested chunks drawn from "
            f"{held.get('n_candidate_chunks')} pre-filter candidates "
            f"(TechCrunch {held['stratification']['techcrunch']}, "
            f"BBC {held['stratification']['bbc']}), new URLs from the existing four "
            "sources. Sampling unit: pre-filter pieces — the surviving database "
            "chunks are filtered by construction and would measure nothing.",
            f"Labels: {labels['status']}; self-consistency disagreements: "
            f"{labels['self_consistency_disagreements']} "
            f"(same labeler, two passes — not inter-annotator agreement).",
            "",
            "| level | precision (95% CI) | recall (95% CI) | TP/FP/FN |",
            "|---|---|---|---|",
            f"| chunk | {_fmt_precision(chunk['precision'])} ({_fmt_ci(chunk['precision_ci95'])}) "
            f"| {chunk['recall']:.3f} ({_fmt_ci(chunk['recall_ci95'])}) "
            f"| {chunk['tp']}/{chunk['fp']}/{chunk['fn']} |",
            f"| article | {_fmt_precision(article['precision'])} "
            f"({_fmt_ci(article['precision_ci95'])}) "
            f"| {article['recall']:.3f} ({_fmt_ci(article['recall_ci95'])}) "
            f"| {article['tp']}/{article['fp']}/{article['fn']} "
            f"({article['n_articles']} articles) |",
            "",
            "Threshold trigger (precision < 0.95): "
            f"**{'FIRED' if results['trigger']['fired'] else 'not fired'}**"
            + (f" — {results['trigger']['note']}." if results["trigger"]["note"] else "."),
            "",
        ]
    )


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["collect", "sample", "score"])
    args = parser.parse_args(argv)
    if args.phase == "collect":
        collect()
    elif args.phase == "sample":
        sample_from_log()
    else:
        score()
    return 0


if __name__ == "__main__":
    sys.exit(main())
