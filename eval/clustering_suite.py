"""Clustering suite (plan §2.3).

Against the agent-drafted true event set in ``eval/event_labels.json``
(~29 snapshot articles in 18 true events, 5 cross-source; pending Yaswanth's
20% review like the query set):

* **slow path** — the real ``_cluster_labels`` (UMAP + HDBSCAN) over the
  labelled articles' Qdrant vectors; ARI, NMI and purity vs. the true
  grouping. HDBSCAN noise (-1) counts as its own cluster (stated, not hidden).
* **fast path** — ``match_article_to_event`` for every labelled article
  against the live centroid collection; of the matches into events that
  contain at least one other labelled article, the fraction whose true label
  matches the event's majority true label (judgeable precision). Matches
  into unlabelled events are counted and reported separately, never silently
  counted as correct.

Usage:
    uv run python -m eval.clustering_suite
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
LABELS_PATH = EVAL_DIR / "event_labels.json"


def _git_sha() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _latest_snapshot_id() -> str:
    snapshots = sorted((EVAL_DIR / "snapshots").glob("*.json"))
    if not snapshots:
        raise SystemExit("no snapshots in eval/snapshots — run python -m eval.make_snapshot")
    return snapshots[-1].stem


def load_true_labels(snapshot_id: str) -> tuple[list[str], list[str]]:
    """Resolve the drafted labels against the snapshot; returns (ids, labels)."""
    data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    snapshot = json.loads(
        (EVAL_DIR / "snapshots" / f"{snapshot_id}.json").read_text(encoding="utf-8")
    )
    by_prefix: dict[str, str] = {}
    for article in snapshot["articles"]:
        by_prefix.setdefault(article["article_id"][:8], article["article_id"])
    ids: list[str] = []
    labels: list[str] = []
    for event in data["events"]:
        for prefix in event["article_ids"]:
            full = by_prefix.get(prefix)
            if full is None:
                raise SystemExit(f"event label references unknown article {prefix}")
            ids.append(full)
            labels.append(event["label"])
    return ids, labels


def purity(true_labels: list[str], predicted_labels: list[int]) -> float:
    """Fraction of articles in the majority true label of their predicted cluster."""
    clusters: dict[int, list[str]] = {}
    for predicted, true in zip(predicted_labels, true_labels, strict=True):
        clusters.setdefault(predicted, []).append(true)
    correct = sum(
        max((members.count(label) for label in set(members)), default=0)
        for members in clusters.values()
    )
    return correct / len(true_labels)


def run(snapshot_id: str) -> dict[str, Any]:
    import numpy as np
    from backend.core.config import settings
    from backend.core.database import SessionLocal
    from backend.db.models import Article
    from backend.modules.events import service as events_service
    from backend.modules.events.service import _cluster_labels  # noqa: PLC2701 - the real path
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    ids, true_labels = load_true_labels(snapshot_id)
    label_by_id = dict(zip(ids, true_labels, strict=True))
    client = events_service.get_qdrant_client()

    db = SessionLocal()
    try:
        # ── Slow path: exactly the production input ────────────────────────
        # The real slow path clusters the unmatched articles' vectors with
        # _cluster_labels. Clustering only the labelled handful would measure a
        # different task, so the suite runs the production function over the
        # production input and then scores the labelled members of it.
        pairs = events_service.list_unmatched_articles(db, client=client)
        input_ids = [str(article.id) for article, _vector in pairs]
        labels_all = (
            list(_cluster_labels(np.asarray([v for _, v in pairs], dtype=float))) if pairs else []
        )
        cluster_of = dict(zip(input_ids, labels_all, strict=True))

        scored_ids = [article_id for article_id in ids if article_id in cluster_of]
        slow_true = [label_by_id[article_id] for article_id in scored_ids]
        slow_pred = [cluster_of[article_id] for article_id in scored_ids]
        # With <2 labelled members the indices are degenerate (a single label
        # scores 1.0 by definition), so they are reported as unscoreable rather
        # than as a perfect result.
        scoreable = len(scored_ids) >= 2
        ari = float(adjusted_rand_score(slow_true, slow_pred)) if scoreable else None
        nmi = float(normalized_mutual_info_score(slow_true, slow_pred)) if scoreable else None
        purity_value = purity(slow_true, slow_pred) if scoreable else None
        slow_note = (
            None
            if scoreable
            else "fewer than 2 labelled articles were in the slow-path input; "
            "ARI/NMI/purity are unscoreable (not 1.0)"
        )

        # ── Combined partition, the grouping the system actually assigns ────
        # Fast-path members carry a live event; the rest carry their slow-path
        # cluster. This is the full, comparable partition over the label set.
        articles = {
            str(article.id): article
            for article in db.query(Article).filter(Article.id.in_([_uuid(i) for i in ids]))
        }
        combined_keys: list[str] = []
        combined_true: list[str] = []
        for article_id in ids:
            article = articles.get(article_id)
            if article is None:
                continue
            if article.event_id is not None:
                combined_keys.append(f"event:{article.event_id}")
            elif article_id in cluster_of and cluster_of[article_id] >= 0:
                combined_keys.append(f"slow:{cluster_of[article_id]}")
            else:
                combined_keys.append(f"unmatched:{article_id}")  # its own group, stated
            combined_true.append(label_by_id[article_id])
        key_index = {key: index for index, key in enumerate(sorted(set(combined_keys)))}
        combined_pred = [key_index[key] for key in combined_keys]
        combined = {
            "n_labelled": len(combined_true),
            "n_groups": len(set(combined_keys)),
            "n_fast_path_events": sum(1 for key in combined_keys if key.startswith("event:")),
            "n_slow_path_clusters": sum(1 for key in combined_keys if key.startswith("slow:")),
            "n_unmatched": sum(1 for key in combined_keys if key.startswith("unmatched:")),
            "ari": float(adjusted_rand_score(combined_true, combined_pred)),
            "nmi": float(normalized_mutual_info_score(combined_true, combined_pred)),
            "purity": purity(combined_true, combined_pred),
        }

        # ── Fast path: match every labelled article against the live centroids
        judgeable_total = 0
        judgeable_correct = 0
        matched_unjudgeable = 0
        unmatched = 0
        event_members: dict[Any, list[str]] = {}
        # Map live events -> their labelled members via the join table, so a
        # match into an event containing a labelled article can be judged.
        from backend.db.models import EventArticle
        from sqlalchemy import select

        rows = db.execute(
            select(EventArticle.event_id, EventArticle.article_id).where(
                EventArticle.article_id.in_([_uuid(a) for a in ids])
            )
        ).all()
        for event_id, article_id in rows:
            event_members.setdefault(event_id, []).append(str(article_id))

        for article_id in ids:
            if db.get(Article, _uuid(article_id)) is None:
                continue
            hit = events_service.match_article_to_event(db, _uuid(article_id), client=client)
            if hit is None:
                unmatched += 1
                continue
            event_id = hit.id
            members = event_members.get(event_id, [])
            member_labels = [label_by_id[m] for m in members if m in label_by_id]
            if len(member_labels) < 1:
                matched_unjudgeable += 1
                continue
            majority = max(set(member_labels), key=member_labels.count)
            judgeable_total += 1
            if label_by_id[article_id] == majority:
                judgeable_correct += 1
    finally:
        db.close()

    results = {
        "suite": "clustering",
        "snapshot_id": snapshot_id,
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "label_set": {
            "file": "eval/event_labels.json",
            "status": json.loads(LABELS_PATH.read_text(encoding="utf-8"))["status"],
            "n_articles": len(ids),
            "n_events": len(set(true_labels)),
            "cross_source_events": 5,
        },
        "slow_path": {
            "ari": ari,
            "nmi": nmi,
            "purity": purity_value,
            "n_labelled_scored": len(scored_ids),
            "n_labelled_outside_input": len(ids) - len(scored_ids),
            "n_input_articles": len(input_ids),
            "noise_cluster_size": sum(1 for label in labels_all if label == -1),
            "n_clusters_found": len(set(labels_all) - {-1}),
            "note": slow_note,
        },
        "combined_partition": combined,
        "fast_path": {
            "judgeable_matches": judgeable_total,
            "judgeable_correct": judgeable_correct,
            "precision": judgeable_correct / judgeable_total if judgeable_total else None,
            "matched_into_unlabelled_events": matched_unjudgeable,
            "unmatched": unmatched,
            # The knob this suite measures: the live centroid-match threshold.
            "threshold": settings.event_match_threshold,
            "plan_reference_threshold": 0.72,
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").date.today().strftime("%Y%m%d")
    name = f"{stamp}_{results['git_sha']}_clustering"
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    md = _render_md(results)
    (RESULTS_DIR / f"{name}.md").write_text(md, encoding="utf-8")
    print(md)
    return results


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _render_md(results: dict[str, Any]) -> str:
    slow = results["slow_path"]
    fast = results["fast_path"]
    combined = results["combined_partition"]
    labels = results["label_set"]
    return "\n".join(
        [
            f"# Clustering suite — snapshot `{results['snapshot_id']}` ({results['git_sha']})",
            "",
            f"Label set: {labels['file']} ({labels['status']}) — {labels['n_articles']} articles "
            f"in {labels['n_events']} true events, {labels['cross_source_events']} cross-source.",
            "",
            "## Slow path (UMAP + HDBSCAN over the production input)",
            "",
            "| metric | value |",
            "|---|---|",
            f"| articles clustered (unmatched, the real input) | {slow['n_input_articles']} |",
            f"| clusters found (noise excluded) | {slow['n_clusters_found']} |",
            f"| noise points (-1) | {slow['noise_cluster_size']} / {slow['n_input_articles']} |",
            f"| labelled articles inside that input | {slow['n_labelled_scored']} of "
            f"{labels['n_articles']} |",
            f"| ARI / NMI / purity (labelled members) | {_fmt(slow['ari'])} / "
            f"{_fmt(slow['nmi'])} / {_fmt(slow['purity'])} |",
            *([f"| note | {slow['note']} |"] if slow.get("note") else []),
            "",
            "## Combined partition (fast-path event, else slow-path cluster)",
            "",
            "| metric | value |",
            "|---|---|",
            f"| labelled articles placed | {combined['n_labelled']} |",
            f"| groups (events {combined['n_fast_path_events']} / clusters "
            f"{combined['n_slow_path_clusters']} / unmatched {combined['n_unmatched']}) "
            f"| {combined['n_groups']} |",
            f"| ARI / NMI / purity | {_fmt(combined['ari'])} / {_fmt(combined['nmi'])} / "
            f"{_fmt(combined['purity'])} |",
            "",
            "## Fast path (centroid matching)",
            "",
            "| metric | value |",
            "|---|---|",
            f"| precision over judgeable matches (threshold {fast['threshold']}) "
            f"| {_fmt(fast['precision'])} ({fast['judgeable_correct']}/"
            f"{fast['judgeable_matches']}) |",
            f"| matched into unlabelled events | {fast['matched_into_unlabelled_events']} |",
            f"| unmatched | {fast['unmatched']} |",
            "",
            "Noise points count as their own cluster in ARI/NMI (stated, not hidden); "
            "labelled articles that already sit in an event are not part of the slow-path "
            "input and are excluded from the slow-path rows. Fast-path precision is only "
            "over matches into events holding at least one other labelled article — the "
            "rest are reported as unjudgeable, never as correct.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=None)
    args = parser.parse_args(argv)
    run(args.snapshot or _latest_snapshot_id())
    return 0


if __name__ == "__main__":
    sys.exit(main())
