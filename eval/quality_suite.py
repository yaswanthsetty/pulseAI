"""Content-quality suite: honest held-out measurement of the boilerplate filter.

The Phase 1.2 numbers (P=0.984 / R=0.969) are in-sample; this suite measures
the same classifier on **freshly ingested chunks it has never seen** (plan
§2.4): new URLs from the existing four sources only, stratified with at
>=15 TechCrunch and >=15 BBC chunks (the junk-heavy sources). Labels are
drafted by the coding agent in two passes with a cool-off (self-consistency,
not inter-annotator agreement) and reviewed by Yaswanth.

Phases:

* ``collect`` — poll each active source once (new URLs only; dedupe keeps the
  corpus honest), synchronously process the new articles, then sample a
  stratified ~50-chunk held-out set into ``eval/work/heldout_set.json``.
* ``score``  — chunk-level precision/recall of ``is_boilerplate`` vs the
  final human labels, with 95% Wilson intervals (never hidden). Trigger for
  revisiting the threshold: held-out precision < 0.95.

The two labelling passes live in ``eval/heldout_labels.json``; the score
phase refuses to run until both passes exist for every chunk.

Usage:
    uv run python -m eval.quality_suite collect
    uv run python -m eval.quality_suite score
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
WORK_DIR = EVAL_DIR / "work"
RESULTS_DIR = EVAL_DIR / "results"
HELDOUT_PATH = WORK_DIR / "heldout_set.json"
LABELS_PATH = EVAL_DIR / "heldout_labels.json"

TARGET_SIZE = 50
JUNK_HEAVY_MIN = 15  # >=15 TechCrunch and >=15 BBC chunks (plan §2.4)


def _git_sha() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def collect() -> Path:
    """Poll every active source once; sample a stratified held-out chunk set."""
    from backend.core.database import SessionLocal
    from backend.db.models import Article, ArticleChunk, Source
    from backend.modules.ingestion import service as ingestion
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
            # Process synchronously (fetch + extract + chunk + classify) so the
            # suite does not depend on the RQ worker being alive.
            for article_id in added:
                try:
                    ingestion.process_article(db, article_id)
                except Exception as exc:  # noqa: BLE001 - one bad article must not sink the run
                    poll_log[-1]["errors"] = poll_log[-1].get("errors", 0) + 1
                    print(f"  process_article failed for {article_id}: {exc}")

        if not new_ids:
            raise SystemExit(
                "no new articles ingested — feeds have no unseen URLs right now. "
                f"poll log: {poll_log}"
            )

        # Pull every chunk of the new articles.
        rows = db.execute(
            select(ArticleChunk, Article, Source)
            .join(Article, ArticleChunk.article_id == Article.id)
            .join(Source, Article.source_id == Source.id)
            .where(Article.id.in_([_uuid(i) for i in new_ids]))
        ).all()

        by_source: dict[str, list[dict[str, Any]]] = {}
        for chunk, article, source in rows:
            by_source.setdefault(source.name, []).append(
                {
                    "chunk_id": str(chunk.id),
                    "article_id": str(article.id),
                    "source_name": source.name,
                    "title": article.title,
                    "extractor": article.extractor,
                    "extraction_quality": article.extraction_quality,
                    "text": chunk.chunk_text[:1500],
                }
            )

        # Stratify: >=15 from each junk-heavy source first, then fill to target.
        sample: list[dict[str, Any]] = []
        for name, chunks in by_source.items():
            if "techcrunch" in name.lower() or "bbc" in name.lower():
                sample.extend(chunks[:JUNK_HEAVY_MIN])
        remaining = TARGET_SIZE - len(sample)
        if remaining > 0:
            others = [
                c
                for name, chunks in by_source.items()
                if "techcrunch" not in name.lower() and "bbc" not in name.lower()
                for c in chunks
            ]
            sample.extend(others[:remaining])
        sample = sample[:TARGET_SIZE]

        tc = sum(1 for c in sample if "techcrunch" in c["source_name"].lower())
        bbc = sum(1 for c in sample if "bbc" in c["source_name"].lower())
        if tc < JUNK_HEAVY_MIN or bbc < JUNK_HEAVY_MIN:
            print(f"WARNING: stratification below target (techcrunch={tc}, bbc={bbc})")

        payload = {
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "git_sha": _git_sha(),
            "poll_log": poll_log,
            "n_new_articles": len(new_ids),
            "sample": sample,
            "stratification": {"techcrunch": tc, "bbc": bbc, "total": len(sample)},
        }
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        HELDOUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"held-out set : {HELDOUT_PATH} ({len(sample)} chunks; tc={tc}, bbc={bbc})")
        return HELDOUT_PATH
    finally:
        db.close()


def score() -> dict[str, Any]:
    """Chunk-level precision/recall of the classifier vs final human labels."""
    from backend.core.content_quality import boilerplate_score, is_boilerplate

    from eval.stats import precision_recall

    heldout = json.loads(HELDOUT_PATH.read_text(encoding="utf-8"))
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))

    missing = [c["chunk_id"] for c in heldout["sample"] if c["chunk_id"] not in labels]
    if missing:
        raise SystemExit(f"{len(missing)} chunks unlabelled — run both labelling passes first")
    incomplete = [
        cid
        for cid, row in labels.items()
        if row.get("pass1") is None or row.get("pass2") is None or row.get("final") is None
    ]
    if incomplete:
        raise SystemExit(f"{len(incomplete)} chunks missing a labelling pass")

    predicted: list[bool] = []
    truth: list[bool] = []
    disagreements: list[str] = []
    scores: list[float] = []
    for chunk in heldout["sample"]:
        row = labels[chunk["chunk_id"]]
        if row["pass1"] != row["pass2"]:
            disagreements.append(chunk["chunk_id"])
        predicted.append(is_boilerplate(chunk["text"]))
        truth.append(bool(row["final"]))
        scores.append(boilerplate_score(chunk["text"]))

    metrics = precision_recall(predicted, truth)

    # Article-level view: extraction_quality=low vs any-boilerplate human call.
    by_article: dict[str, dict[str, Any]] = {}
    for chunk, is_truth in zip(heldout["sample"], truth, strict=True):
        entry = by_article.setdefault(
            chunk["article_id"],
            {"predicted_low": chunk["extraction_quality"] == "low", "any_boilerplate": False},
        )
        entry["any_boilerplate"] = entry["any_boilerplate"] or is_truth
    article_rows = list(by_article.values())
    article_metrics = precision_recall(
        [r["predicted_low"] for r in article_rows], [r["any_boilerplate"] for r in article_rows]
    )

    results = {
        "suite": "quality",
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "heldout": {
            "file": "eval/work/heldout_set.json",
            "created_at": heldout["created_at"],
            "n_chunks": len(heldout["sample"]),
            "stratification": heldout["stratification"],
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
        "article_level": article_metrics,
        "trigger": {
            "rule": "revisit BOILERPLATE_THRESHOLD if held-out precision < 0.95",
            "fired": metrics["precision"] < 0.95,
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


def _render_md(results: dict[str, Any]) -> str:
    chunk = results["chunk_level"]
    article = results["article_level"]
    held = results["heldout"]
    labels = results["labels"]
    return "\n".join(
        [
            f"# Content-quality suite (held-out) — {results['git_sha']}",
            "",
            f"Held-out set: {held['n_chunks']} freshly ingested chunks "
            f"(TechCrunch {held['stratification']['techcrunch']}, "
            f"BBC {held['stratification']['bbc']}), new URLs from the existing four sources.",
            f"Labels: {labels['status']}; self-consistency disagreements: "
            f"{labels['self_consistency_disagreements']} "
            f"(same labeler, two passes — not inter-annotator agreement).",
            "",
            "| level | precision (95% CI) | recall (95% CI) | TP/FP/FN |",
            "|---|---|---|---|",
            f"| chunk | {chunk['precision']:.3f} ({_fmt_ci(chunk['precision_ci95'])}) "
            f"| {chunk['recall']:.3f} ({_fmt_ci(chunk['recall_ci95'])}) "
            f"| {chunk['tp']}/{chunk['fp']}/{chunk['fn']} |",
            f"| article | {article['precision']:.3f} ({_fmt_ci(article['precision_ci95'])}) "
            f"| {article['recall']:.3f} ({_fmt_ci(article['recall_ci95'])}) "
            f"| {article['tp']}/{article['fp']}/{article['fn']} |",
            "",
            "Threshold trigger (precision < 0.95): "
            f"**{'FIRED' if results['trigger']['fired'] else 'not fired'}**.",
            "",
        ]
    )


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["collect", "score"])
    args = parser.parse_args(argv)
    if args.phase == "collect":
        collect()
    else:
        score()
    return 0


if __name__ == "__main__":
    sys.exit(main())
