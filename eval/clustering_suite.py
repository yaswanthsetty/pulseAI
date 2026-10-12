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
    from backend.modules.events import service as events_service
    from backend.modules.events.service import _cluster_labels  # noqa: PLC2701 - the real path

    ids, true_labels = load_true_labels(snapshot_id)
    client = events_service.get_qdrant_client()

    vectors: list[list[float]] = []
    missing: list[str] = []
    for article_id in ids:
        vector = events_service.article_vector(client, article_id)
        if vector is None:
            missing.append(article_id)
        else:
            vectors.append(vector)

    import numpy as np
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    kept_labels = [
        label
        for label, article_id in zip(true_labels, ids, strict=True)
        if article_id not in missing
    ]
    predicted = list(_cluster_labels(np.asarray(vectors, dtype=float)))
    ari = float(adjusted_rand_score(kept_labels, predicted))
    nmi = float(normalized_mutual_info_score(kept_labels, predicted))
    purity_value = purity(kept_labels, predicted)
    n_noise = sum(1 for p in predicted if p == -1)

    # Fast path: match every labelled article against the live centroids.
    from backend.core.database import SessionLocal
    from backend.db.models import Article

    db = SessionLocal()
    judgeable_total = 0
    judgeable_correct = 0
    matched_unjudgeable = 0
    unmatched = 0
    try:
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

        label_by_id = dict(zip(ids, true_labels, strict=True))
        for article_id in ids:
            article = db.get(Article, _uuid(article_id))
            if article is None:
                continue
            hit = events_service.match_article_to_event(db, client, article)
            if hit is None:
                unmatched += 1
                continue
            event_id = hit if not hasattr(hit, "id") else hit.id
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
            "n_labelled_scored": len(kept_labels),
            "missing_vectors": missing,
            "noise_cluster_size": n_noise,
            "n_clusters_found": len(set(predicted) - {-1}),
        },
        "fast_path": {
            "judgeable_matches": judgeable_total,
            "judgeable_correct": judgeable_correct,
            "precision": judgeable_correct / judgeable_total if judgeable_total else None,
            "matched_into_unlabelled_events": matched_unjudgeable,
            "unmatched": unmatched,
            "threshold": 0.72,
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
    labels = results["label_set"]
    return "\n".join(
        [
            f"# Clustering suite — snapshot `{results['snapshot_id']}` ({results['git_sha']})",
            "",
            f"Label set: {labels['file']} ({labels['status']}) — {labels['n_articles']} articles "
            f"in {labels['n_events']} true events, {labels['cross_source_events']} cross-source.",
            "",
            "| metric | value |",
            "|---|---|",
            f"| ARI (slow path, UMAP+HDBSCAN) | {_fmt(slow['ari'])} |",
            f"| NMI | {_fmt(slow['nmi'])} |",
            f"| purity | {_fmt(slow['purity'])} |",
            f"| clusters found (noise excluded) | {slow['n_clusters_found']} |",
            f"| noise points (-1) | {slow['noise_cluster_size']} / {slow['n_labelled_scored']} |",
            f"| fast-path centroid precision (judgeable) | {_fmt(fast['precision'])} "
            f"({fast['judgeable_correct']}/{fast['judgeable_matches']}) |",
            f"| matched into unlabelled events | {fast['matched_into_unlabelled_events']} |",
            f"| unmatched | {fast['unmatched']} |",
            "",
            "Noise points count as their own cluster in ARI/NMI (stated, not hidden). "
            "Fast-path precision is only over matches into events with at least one "
            "other labelled article; the rest are reported as unjudgeable.",
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
