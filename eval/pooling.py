"""Blinded relevance-pool construction (plan §1.3 step 2).

The pool for a query is the union of every ablation config's surfaced chunks
plus the labeler's reading picks, deduplicated by chunk id, then shuffled with
a per-query seeded RNG so the grader never sees which config returned a chunk.
"""

from __future__ import annotations


def build_blinded_pool(
    surfaced_chunk_ids: set[str],
    labeler_pick_ids: set[str],
    *,
    seed_key: str,
) -> list[dict]:
    """Deterministic blinded pool: sorted, seeded-shuffled, indexed."""
    import random

    union = set(surfaced_chunk_ids) | {str(cid) for cid in labeler_pick_ids}
    ordered = sorted(union)
    random.Random(seed_key).shuffle(ordered)
    return [{"pool_index": i, "chunk_id": cid} for i, cid in enumerate(ordered)]


def pool_stats(pools: dict[str, list[dict]]) -> dict:
    """Pool size statistics reported in every results file."""
    import statistics

    sizes = [len(pool) for pool in pools.values()]
    return {
        "queries": len(pools),
        "mean_pool_size": round(statistics.mean(sizes), 2),
        "median_pool_size": statistics.median(sizes),
        "min_pool_size": min(sizes),
        "max_pool_size": max(sizes),
    }
