"""The mocked 12-query CI subset (plan §5).

Same harness arithmetic and wiring as the full suite — pool building, the
draft-grading rule, retrieval metrics, citation metrics, refusal routing —
with a deterministic fake retriever and canned LLM/judge output. No Ollama,
no Qdrant, no network, no DB.

**CI numbers are not quality numbers.** The output is byte-for-byte
reproducible, so CI asserts EXACT equality against the committed
``eval/results/baseline_ci.json``. A deviation means the *harness* changed.

The query set is read from the committed fixture
``tests/fixtures/eval/queries.jsonl`` (regenerate with ``python -m eval.mock``),
so editing the CI subset is a visible diff, not a silent code change.

Usage:
    uv run python -m eval.ci_subset                 # print summary
    uv run python -m eval.ci_subset --write-baseline  # (re)generate the baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval import metrics as M
from eval.grading import draft_grade
from eval.mock import (
    canned_chat_deep,
    canned_chat_fast,
    canned_judge,
    load_fixture_queries,
    make_fake_searcher,
)
from eval.pooling import build_blinded_pool, pool_stats
from eval.rag_suite import _metric_set
from eval.retrieval_suite import CONFIGS

EVAL_DIR = Path(__file__).resolve().parent
BASELINE_PATH = EVAL_DIR / "results" / "baseline_ci.json"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eval"


def run(limit: int | None = None) -> dict[str, Any]:
    """Produce the full deterministic metric dict for the 12-query subset."""
    queries = load_fixture_queries()
    if limit:
        queries = queries[:limit]
    search = make_fake_searcher()

    # --- retrieval half: 12 configs x 12 queries, pools, draft grades -------
    per_config: dict[str, list[dict[str, float | None]]] = {c["config_id"]: [] for c in CONFIGS}
    pools: dict[str, list[dict[str, Any]]] = {}
    grades_by_query: dict[str, dict[str, int]] = {}

    for query in queries:
        surfaced: set[str] = set()
        rankings: dict[str, list[str]] = {}
        for config in CONFIGS:
            results = search(
                query["query"],
                limit=10,
                mode=config["mode"],
                config_id=config["config_id"],
            )
            ranked = [str(r.chunk_id) for r in results]
            rankings[config["config_id"]] = ranked
            surfaced.update(ranked)
        pools[query["id"]] = build_blinded_pool(
            surfaced,
            {str(c) for c in query["relevant_chunk_ids"]},
            seed_key=f"mock:{query['id']}",
        )

    # Grade every pooled chunk with the shared deterministic rule. The text
    # comes from the canned article facts/decoys (no DB needed).
    from eval.mock import FAKE_ARTICLES, _chunk_id

    article_of = {
        _chunk_id(article_id, slot): (article_id, slot)
        for article_id in FAKE_ARTICLES
        for slot in ("fact", "decoy")
    }
    for query in queries:
        grades: dict[str, int] = {}
        for entry in pools[query["id"]]:
            cid = entry["chunk_id"]
            article_id, slot = article_of[cid]
            grade, _source = draft_grade(
                FAKE_ARTICLES[article_id][slot],
                list(query["required_facts"]),
                is_labeler_pick_chunk=cid in {str(c) for c in query["relevant_chunk_ids"]},
                is_labeler_pick_article=article_id in set(query["relevant_article_ids"]),
            )
            grades[cid] = grade
        grades_by_query[query["id"]] = grades

    for query in queries:
        grades = grades_by_query[query["id"]]
        for config in CONFIGS:
            per_config[config["config_id"]].append(
                M.evaluate_ranking(rankings_of(queries, query, config, search), grades)
            )

    retrieval_agg = {cid: M.aggregate(rows) for cid, rows in per_config.items()}

    # --- rag half: canned fast/deep records through the real metric set -----
    judge = canned_judge()
    per_path: dict[str, list[dict[str, Any]]] = {"fast": [], "deep": []}
    subq_scores: list[float] = []
    for query in queries:
        for path, maker in (("fast", canned_chat_fast), ("deep", canned_chat_deep)):
            record = maker(query)
            record["query_id"] = query["id"]
            record["message"] = query["query"]
            row = _metric_set(
                record, grades_by_query[query["id"]], list(query["required_facts"]), judge
            )
            per_path[path].append({"query_id": query["id"], **row})
            if path == "deep":
                for sub_q in record["sub_questions"]:
                    result = judge.judge(
                        "sub_question_relevance",
                        {"USER QUERY": query["query"], "SUB-QUESTION": sub_q},
                    )
                    if result["score"] is not None:
                        subq_scores.append(result["score"])

    rag_agg: dict[str, Any] = {}
    for path, rows in per_path.items():
        numeric = [
            "citation_precision",
            "citation_coverage",
            "required_fact_recall",
            "invalid_citation_rate",
        ]
        agg: dict[str, Any] = {key: M.macro_mean([row[key] for row in rows]) for key in numeric}
        agg["n_queries"] = len(rows)
        agg["refusal_count"] = sum(1 for row in rows if row["refusal"])
        agg["judge_routed_count"] = sum(1 for row in rows if row["judge_routed"])
        rag_agg[path] = agg

    results = {
        "suite": "ci_mock",
        "note": "CI numbers are not quality numbers — harness arithmetic only.",
        "n_queries": len(queries),
        "pool": pool_stats(pools),
        "retrieval_per_config": retrieval_agg,
        "rag": rag_agg,
        "sub_question_relevance": M.macro_mean(subq_scores),
        "judge": judge.version,
    }
    return results


def rankings_of(queries, query, config, search) -> list[str]:
    """Recompute one config's ranking (deterministic; keeps run() readable)."""
    results = search(query["query"], limit=10, mode=config["mode"], config_id=config["config_id"])
    return [str(r.chunk_id) for r in results]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    results = run(limit=args.limit)
    if args.write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"baseline written: {BASELINE_PATH}")
        return 0
    print(json.dumps(results, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
