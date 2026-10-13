"""Retrieval suite: 12-config ablation over the real search path + pooled labels.

Phases (see docs/plans/phase2_eval.md §2.1 and §1.3):

* ``collect`` — run every query through the real ``retrieval.service.search``
  under all 12 ablation configs (dense/sparse/hybrid x rerank on/off x
  temporal on/off), record the ranked top-10 (one primary chunk per result),
  and build the blinded relevance pool per query: the union of every config's
  surfaced chunks (primaries + grounding extras, depth 10) plus the chunks
  the labeler picked by reading. Pool order is shuffled with a per-query
  seeded RNG so the grader never sees which config returned a chunk.
* ``grade`` — deterministic draft grades for every pooled chunk
  (2 = contains a required fact, 1 = same article as a labeler pick,
  0 = otherwise), plus reading-based overrides from
  ``eval/label_overrides.jsonl``. Writes ``eval/labels.jsonl``. Grades are
  agent-drafted and gated on Yaswanth's 20% review before the set freezes.
* ``score`` — Recall@5/10, MRR@10, nDCG@10 per config, macro-averaged with a
  per-type breakdown. Unjudged chunks count as 0. Writes
  ``eval/results/<date>_<sha7>.{json,md}`` + ``latest.json``.

Usage:
    uv run python -m eval.retrieval_suite collect [--limit N]
    uv run python -m eval.retrieval_suite grade
    uv run python -m eval.retrieval_suite score
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
SNAP_DIR = EVAL_DIR / "snapshots"
WORK_DIR = EVAL_DIR / "work"
RESULTS_DIR = EVAL_DIR / "results"
QUERIES_PATH = EVAL_DIR / "queries.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"
OVERRIDES_PATH = EVAL_DIR / "label_overrides.jsonl"

POOL_DEPTH = 10  # top-N per config folded into the pool
MAX_CHUNKS_PER_ARTICLE = 4  # grounding extras visible for pooling

# The 12 ablation configs from the plan: mode x rerank x temporal.
MODES = ("semantic", "keyword", "hybrid")
CONFIGS: list[dict[str, Any]] = [
    {
        "config_id": f"{mode}-{'rerank' if rerank else 'norank'}-"
        f"{'temporal' if temporal else 'notemporal'}",
        "mode": mode,
        "rerank": rerank,
        "temporal": temporal,
    }
    for mode in MODES
    for rerank in (False, True)
    for temporal in (False, True)
]
# The production default path (API defaults: semantic, rerank on, temporal auto).
PRODUCTION_CONFIG = "semantic-rerank-temporal"


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _snapshot_path(snapshot_id: str) -> Path:
    return SNAP_DIR / f"{snapshot_id}.json"


def _latest_snapshot_id() -> str:
    snapshots = sorted(SNAP_DIR.glob("*.json"))
    if not snapshots:
        raise SystemExit("no snapshots in eval/snapshots — run python -m eval.make_snapshot")
    return snapshots[-1].stem


def load_queries(snapshot_id: str, *, include_rejected: bool = False) -> list[dict[str, Any]]:
    """Queries for the snapshot; human-rejected queries are excluded by default.

    ``status == "rejected"`` is set by ``eval.label --apply`` from Yaswanth's
    review: a query the reviewer threw out must not be scored. ``include_rejected``
    exists so the reported set status can still describe the whole file.
    """
    queries = [
        json.loads(line) for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines() if line
    ]
    mismatched = [q["id"] for q in queries if q.get("snapshot") != snapshot_id]
    if mismatched:
        raise SystemExit(f"queries not on snapshot {snapshot_id}: {mismatched}")
    if include_rejected:
        return queries
    active = [q for q in queries if q.get("status") != "rejected"]
    if not active:
        raise SystemExit("every query in eval/queries.jsonl is rejected — nothing to score")
    return active


def load_snapshot_articles(snapshot_id: str) -> dict[str, dict[str, Any]]:
    data = json.loads(_snapshot_path(snapshot_id).read_text(encoding="utf-8"))
    return {a["article_id"]: a for a in data["articles"]}


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def collect(snapshot_id: str, limit: int | None = None) -> Path:
    """Run every query x config through the real search path; build pools."""
    # Imported lazily so --help and the unit tests need no heavy deps.
    from backend.core.config import settings
    from backend.modules.retrieval import service as retrieval

    queries = load_queries(snapshot_id)
    if limit:
        queries = queries[:limit]

    embedder = retrieval.get_embedder()  # warm the model once, timed below
    retrieval.get_qdrant_client()
    t0 = time.monotonic()
    if any(c["rerank"] for c in CONFIGS):
        retrieval.get_reranker()
    print(
        f"models warm in {time.monotonic() - t0:.1f}s; "
        f"{len(queries)} queries x {len(CONFIGS)} configs"
    )

    per_query: list[dict[str, Any]] = []
    pool_texts: dict[str, dict[str, Any]] = {}
    original_rerank = settings.rerank_enabled

    def _note_pool_text(
        chunk_id: str, payload: dict[str, Any], article: dict[str, Any] | None
    ) -> None:
        if chunk_id in pool_texts or not payload:
            return
        pool_texts[chunk_id] = {
            "article_id": payload.get("article_id"),
            "title": payload.get("title") or (article or {}).get("title", ""),
            "source_name": payload.get("source_name") or (article or {}).get("source_name", ""),
            "chunk_number": payload.get("chunk_number"),
            "text": (payload.get("chunk_text") or "")[:1500],
        }

    try:
        for index, query in enumerate(queries, start=1):
            qid = query["id"]
            records: list[dict[str, Any]] = []
            surfaced: dict[str, dict[str, Any]] = {}  # chunk_id -> best payload seen
            for config in CONFIGS:
                settings.rerank_enabled = config["rerank"]
                started = time.monotonic()
                results = retrieval.search(
                    query=query["query"],
                    limit=POOL_DEPTH,
                    mode=config["mode"],
                    intent=None if config["temporal"] else "default",
                    embedder=embedder,
                    max_chunks_per_article=MAX_CHUNKS_PER_ARTICLE,
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                ranked_chunk_ids: list[str] = []
                result_rows: list[dict[str, Any]] = []
                extra_rows: list[dict[str, Any]] = []
                for rank, result in enumerate(results, start=1):
                    chunk_id = str(result.chunk_id) if result.chunk_id else None
                    if chunk_id is None:
                        continue
                    ranked_chunk_ids.append(chunk_id)
                    result_rows.append(
                        {
                            "rank": rank,
                            "chunk_id": chunk_id,
                            "article_id": str(result.article_id),
                            "score": round(float(result.similarity_score), 6),
                        }
                    )
                    surfaced.setdefault(
                        chunk_id,
                        {
                            "chunk_id": chunk_id,
                            "article_id": str(result.article_id),
                            "title": result.title,
                            "source_name": result.source_name,
                            "chunk_number": result.chunk_index,
                            "text": (result.chunk_text or "")[:1500],
                        },
                    )
                    for extra in result.extra_chunks:
                        extra_id = str(extra.chunk_id)
                        extra_rows.append(
                            {"chunk_id": extra_id, "article_id": str(result.article_id)}
                        )
                        surfaced.setdefault(
                            extra_id,
                            {
                                "chunk_id": extra_id,
                                "article_id": str(result.article_id),
                                "title": result.title,
                                "source_name": result.source_name,
                                "chunk_number": extra.chunk_index,
                                "text": (extra.chunk_text or "")[:1500],
                            },
                        )
                records.append(
                    {
                        "config_id": config["config_id"],
                        "ranked_chunk_ids": ranked_chunk_ids,
                        "results": result_rows,
                        "extras": extra_rows,
                        "latency_ms": elapsed_ms,
                    }
                )
            per_query.append(
                {
                    "query_id": qid,
                    "type": query["type"],
                    "path_hint": query["path_hint"],
                    "records": records,
                    "surfaced": list(surfaced.values()),
                }
            )
            print(f"  [{index}/{len(queries)}] {qid} done ({len(surfaced)} unique chunks surfaced)")
    finally:
        settings.rerank_enabled = original_rerank

    # Blinded pools: union of surfaced chunks + labeler picks, seeded shuffle.
    from eval.pooling import build_blinded_pool, pool_stats

    query_by_id = {q["id"]: q for q in queries}
    pools: dict[str, list[dict[str, Any]]] = {}
    for entry in per_query:
        query = query_by_id[entry["query_id"]]
        pools[entry["query_id"]] = build_blinded_pool(
            {row["chunk_id"] for row in entry["surfaced"]},
            {str(cid) for cid in query["relevant_chunk_ids"]},
            seed_key=f"{snapshot_id}:{entry['query_id']}",
        )
        for row in entry["surfaced"]:
            pool_texts.setdefault(row["chunk_id"], row)

    # Labeler-picked chunks may not have been surfaced by any config: pull
    # their text from the DB so grading can still read them.
    missing = {
        cid
        for pool in pools.values()
        for entry in pool
        if (cid := entry["chunk_id"]) not in pool_texts
    }
    if missing:
        from backend.core.database import SessionLocal
        from backend.db.models import Article, ArticleChunk, Source
        from sqlalchemy import select

        db = SessionLocal()
        try:
            rows = db.execute(
                select(ArticleChunk, Article, Source)
                .join(Article, ArticleChunk.article_id == Article.id)
                .join(Source, Article.source_id == Source.id)
                .where(ArticleChunk.id.in_([_uuid(cid) for cid in missing]))
            ).all()
            for chunk, article, source in rows:
                pool_texts[str(chunk.id)] = {
                    "article_id": str(article.id),
                    "title": article.title,
                    "source_name": source.name,
                    "chunk_number": chunk.chunk_number,
                    "text": (chunk.chunk_text or "")[:1500],
                }
        finally:
            db.close()

    payload = {
        "snapshot_id": snapshot_id,
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "pool_depth": POOL_DEPTH,
        "max_chunks_per_article": MAX_CHUNKS_PER_ARTICLE,
        "configs": CONFIGS,
        "pool_stats": pool_stats(pools),
        "per_query": per_query,
        "pools": pools,
    }
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = WORK_DIR / f"{snapshot_id}_retrieval_raw.json"
    raw_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    text_path = WORK_DIR / f"{snapshot_id}_pool_text.json"
    text_path.write_text(json.dumps(pool_texts, indent=1), encoding="utf-8")
    print(f"raw records : {raw_path}")
    print(f"pool texts  : {text_path}")
    print(f"pool stats  : {payload['pool_stats']}")
    return raw_path


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


def grade(snapshot_id: str) -> Path:
    """Deterministic draft grades + reading overrides -> eval/labels.jsonl."""
    raw = json.loads((WORK_DIR / f"{snapshot_id}_retrieval_raw.json").read_text(encoding="utf-8"))
    pool_texts = json.loads(
        (WORK_DIR / f"{snapshot_id}_pool_text.json").read_text(encoding="utf-8")
    )
    queries = {q["id"]: q for q in load_queries(snapshot_id)}

    overrides: dict[tuple[str, str], dict[str, Any]] = {}
    if OVERRIDES_PATH.exists():
        for line in OVERRIDES_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            overrides[(row["query_id"], row["chunk_id"])] = row

    from eval.grading import draft_grade

    labels: list[dict[str, Any]] = []
    stats = {"rule_fact": 0, "rule_pick": 0, "rule_zero": 0, "override": 0}
    for qid, pool in raw["pools"].items():
        query = queries[qid]
        facts = list(query["required_facts"])
        relevant_chunks = {str(c) for c in query["relevant_chunk_ids"]}
        relevant_articles = {str(a) for a in query["relevant_article_ids"]}
        for entry in pool:
            cid = entry["chunk_id"]
            override = overrides.get((qid, cid))
            if override is not None:
                grade_value, source = override["grade"], "override"
                stats["override"] += 1
            else:
                text = pool_texts.get(cid, {}).get("text") or ""
                article_id = str(pool_texts.get(cid, {}).get("article_id"))
                grade_value, source = draft_grade(
                    text,
                    facts,
                    is_labeler_pick_chunk=cid in relevant_chunks,
                    is_labeler_pick_article=article_id in relevant_articles,
                )
                stats[source] += 1
            labels.append(
                {
                    "query_id": qid,
                    "chunk_id": cid,
                    "article_id": pool_texts.get(cid, {}).get("article_id"),
                    "grade": grade_value,
                    "source": source,
                }
            )

    LABELS_PATH.write_text("\n".join(json.dumps(row) for row in labels) + "\n", encoding="utf-8")
    grade_counts = {g: sum(1 for r in labels if r["grade"] == g) for g in (0, 1, 2)}
    per_query_g2: dict[str, int] = {}
    for row in labels:
        if row["grade"] == 2:
            per_query_g2[row["query_id"]] = per_query_g2.get(row["query_id"], 0) + 1
    zero_g2 = [qid for qid in raw["pools"] if per_query_g2.get(qid, 0) == 0]
    print(f"labels      : {LABELS_PATH} ({len(labels)} rows)")
    print(f"grade counts: {grade_counts}  sources: {stats}")
    print(f"queries with zero grade-2 chunks: {len(zero_g2)} {zero_g2}")
    return LABELS_PATH


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _article_ranking(records_results: list[dict[str, Any]], grades: dict[str, int]) -> list[str]:
    """Ranked article ids, deduped, keeping first (best-rank) occurrence."""
    seen: list[str] = []
    for row in records_results:
        if row["article_id"] not in seen:
            seen.append(row["article_id"])
    return seen


def score(snapshot_id: str) -> tuple[Path, Path]:
    from eval import metrics
    from eval.label import query_set_status
    from eval.stats import BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED

    raw = json.loads((WORK_DIR / f"{snapshot_id}_retrieval_raw.json").read_text(encoding="utf-8"))
    all_queries = load_queries(snapshot_id, include_rejected=True)
    queries = {q["id"]: q for q in all_queries if q.get("status") != "rejected"}
    rejected = {q["id"] for q in all_queries if q.get("status") == "rejected"}
    labels = [
        json.loads(line) for line in LABELS_PATH.read_text(encoding="utf-8").splitlines() if line
    ]

    grades_by_query: dict[str, dict[str, int]] = {qid: {} for qid in raw["pools"]}
    for row in labels:
        grades_by_query.setdefault(row["query_id"], {})[row["chunk_id"]] = row["grade"]

    config_ids = [c["config_id"] for c in raw["configs"]]
    # Rejected queries leave the raw file untouched: the same filter runs here,
    # so every per-query list stays positionally aligned with active_entries.
    active_entries = [entry for entry in raw["per_query"] if entry["query_id"] not in rejected]
    per_config: dict[str, list[dict[str, Any]]] = {cid: [] for cid in config_ids}
    per_config_articles: dict[str, list[dict[str, Any]]] = {cid: [] for cid in config_ids}
    for entry in active_entries:
        grades = grades_by_query[entry["query_id"]]
        article_grades: dict[str, int] = {}
        for label_row in labels:
            if label_row["query_id"] != entry["query_id"]:
                continue
            aid = label_row["article_id"]
            if aid is None:
                continue
            article_grades[aid] = max(article_grades.get(aid, 0), label_row["grade"])
        for record in entry["records"]:
            cid = record["config_id"]
            per_config[cid].append(metrics.evaluate_ranking(record["ranked_chunk_ids"], grades))
            ranked_articles = _article_ranking(record["results"], grades)
            per_config_articles[cid].append(
                metrics.evaluate_ranking(ranked_articles, article_grades)
            )

    type_of = {qid: q["type"] for qid, q in queries.items()}
    aggregates: dict[str, Any] = {}
    breakdown: dict[str, Any] = {}
    for cid in config_ids:
        aggregates[cid] = metrics.aggregate(per_config[cid])
        by_type: dict[str, Any] = {}
        for qtype in sorted(set(type_of.values())):
            subset = [
                record
                for record, entry in zip(per_config[cid], active_entries, strict=True)
                if entry["type"] == qtype
            ]
            by_type[qtype] = metrics.aggregate(subset)
        breakdown[cid] = by_type
        aggregates[f"{cid}__article_level"] = metrics.aggregate(per_config_articles[cid])

    significance = significance_vs_default(per_config)

    queries_sha = hashlib.sha256(QUERIES_PATH.read_bytes()).hexdigest()
    git_sha = _git_sha()
    stamp = dt.date.today().strftime("%Y%m%d")
    results = {
        "suite": "retrieval",
        "snapshot_id": snapshot_id,
        "git_sha": git_sha,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "queries_file": "eval/queries.jsonl",
        "queries_sha256": queries_sha,
        "query_set_status": query_set_status(all_queries),
        "n_queries_rejected": len(rejected),
        "pool": {
            "depth": raw["pool_depth"],
            "mean_pool_size": raw["pool_stats"]["mean_pool_size"],
            "median_pool_size": raw["pool_stats"]["median_pool_size"],
        },
        "environment": {
            "embedding_model": _settings_value("embedding_model"),
            "reranker_model": _settings_value("reranker_model"),
            "qdrant_url": _settings_value("qdrant_url"),
        },
        "per_config": aggregates,
        "per_config_article_level": {
            cid: aggregates[f"{cid}__article_level"] for cid in config_ids
        },
        "per_config_by_type": breakdown,
        "per_query_metrics": {
            cid: [
                {"query_id": entry["query_id"], **record}
                for record, entry in zip(per_config[cid], active_entries, strict=True)
            ]
            for cid in config_ids
        },
        "pool_size_stats": raw["pool_stats"],
        "significance": {
            "baseline_config": PRODUCTION_CONFIG,
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "metrics": significance,
        },
        "n_queries": len(active_entries),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{stamp}_{git_sha}.json"
    json_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    md_path = RESULTS_DIR / f"{stamp}_{git_sha}.md"
    md_path.write_text(_render_md(results), encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(_render_md(results))
    print(f"written     : {json_path}\n              {md_path}")
    return json_path, md_path


SIGNIFICANCE_METRICS = ("ndcg@10", "recall@10")


def significance_vs_default(
    per_config: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Paired bootstrap of each config's delta over the production default.

    Describes this query set, nothing more: a CI excluding 0 says the gap is
    unlikely to be noise on these 60 queries. No config is chosen from it.
    """
    from eval.stats import paired_bootstrap_delta

    baseline = per_config.get(PRODUCTION_CONFIG)
    if not baseline:
        return {}
    significance: dict[str, dict[str, dict[str, Any]]] = {}
    for metric in SIGNIFICANCE_METRICS:
        rows: dict[str, dict[str, Any]] = {}
        for config_id, records in per_config.items():
            if config_id == PRODUCTION_CONFIG:
                continue
            rows[config_id] = paired_bootstrap_delta(
                [record[metric] for record in records],
                [record[metric] for record in baseline],
            )
        significance[metric] = rows
    return significance


def significance_lines(payload: dict[str, Any]) -> list[str]:
    """Markdown for the bootstrap table (shared by the suite and the baseline)."""
    block = payload.get("significance") or {}
    metrics = block.get("metrics") or {}
    if not metrics:
        return []

    def cell(entry: dict[str, Any] | None) -> tuple[str, str]:
        if not entry or entry.get("delta") is None:
            return "\u2014", "\u2014"
        low, high = entry["ci95"]
        return f"{entry['delta']:+.3f} ({low:+.3f} to {high:+.3f})", "**yes**" if entry[
            "excludes_zero"
        ] else "no"

    configs = sorted({cid for rows in metrics.values() for cid in rows})
    lines = [
        "### Paired bootstrap vs the production default "
        f"(`{block.get('baseline_config')}`, {block.get('resamples')} resamples, "
        f"seed {block.get('seed')})",
        "",
        "Per-query deltas over the queries both rankings define; a CI that excludes 0 is "
        "marked **yes** and a `+` in the ablation table. Descriptive of this query set "
        "only \u2014 no config is selected from it.",
        "",
        "| config | delta nDCG@10 (95% CI) | excludes 0 | delta recall@10 (95% CI) | excludes 0 |",
        "|---|---|---|---|---|",
    ]
    for config_id in configs:
        ndcg = cell(metrics.get("ndcg@10", {}).get(config_id))
        recall = cell(metrics.get("recall@10", {}).get(config_id))
        lines.append(f"| {config_id} | {ndcg[0]} | {ndcg[1]} | {recall[0]} | {recall[1]} |")
    return lines + [""]


def _settings_value(name: str) -> Any:
    from backend.core.config import settings

    return getattr(settings, name, None)


def _render_md(results: dict[str, Any]) -> str:
    metrics_cols = ["recall@5", "recall@10", "mrr@10", "ndcg@10"]
    lines = [
        f"# Retrieval ablation — snapshot `{results['snapshot_id']}` ({results['git_sha']})",
        "",
        f"Queries: {results['n_queries']} (status: **{results['query_set_status']}**) · "
        f"pool depth {results['pool']['depth']}, mean pool size "
        f"{results['pool']['mean_pool_size']} · unjudged chunks count as 0.",
        "",
        "| config | " + " | ".join(metrics_cols) + " |",
        "|---|" + "---|" * len(metrics_cols),
    ]
    marked = _significant_configs(results)
    for cid, agg in results["per_config"].items():
        if cid.endswith("__article_level"):
            continue
        row = [f"{cid} +" if cid in marked else cid]
        for col in metrics_cols:
            value = agg.get(col)
            row.append("—" if value is None else f"{value:.3f}")
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        f"Production default path: `{_production_row(results)}`.",
        ("A `+` marks a config whose nDCG@10 delta vs the default excludes 0." if marked else ""),
        "",
        *significance_lines(results),
        "## Per-type breakdown (production default config, nDCG@10)",
        "",
        "| type | nDCG@10 | n queries |",
        "|---|---|---|",
    ]
    prod = results["per_config_by_type"].get(PRODUCTION_CONFIG, {})
    for qtype, agg in sorted(prod.items()):
        value = agg.get("ndcg@10")
        lines.append(
            f"| {qtype} | {'—' if value is None else f'{value:.3f}'} | {agg.get('ndcg@10_n', 0)} |"
        )
    return "\n".join(lines) + "\n"


def _significant_configs(results: dict[str, Any]) -> set[str]:
    rows = (results.get("significance") or {}).get("metrics", {}).get("ndcg@10", {})
    return {config_id for config_id, entry in rows.items() if entry.get("excludes_zero")}


def _production_row(results: dict[str, Any]) -> str:
    agg = results["per_config"].get(PRODUCTION_CONFIG, {})
    return (
        f"ndcg@10={agg.get('ndcg@10')}, mrr@10={agg.get('mrr@10')}, "
        f"recall@10={agg.get('recall@10')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["collect", "grade", "score"])
    parser.add_argument("--snapshot", default=None, help="snapshot id (default: newest)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N queries")
    args = parser.parse_args(argv)
    snapshot_id = args.snapshot or _latest_snapshot_id()
    if args.phase == "collect":
        collect(snapshot_id, limit=args.limit)
    elif args.phase == "grade":
        grade(snapshot_id)
    else:
        score(snapshot_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
