"""Baseline report: assemble the per-suite results into one committed file.

Plan §4/§6: ``eval/results/<yyyymmdd>_<gitsha7>_baseline.json`` plus the
matching ``.md`` are the Phase 2 baseline a human reads, and
``eval/results/latest.json`` points at the newest one for comparison runs.
The per-suite files stay in place — this module only aggregates what the
suites already wrote, so a suite can be re-run on its own without re-running
the three-hour RAG pass.

Each suite carries its own ``git_sha``: they can differ when a suite was
re-run after a harness commit, and pretending otherwise would be worse. The
top-level ``git_sha`` is the revision that assembled the file.

Usage:
    uv run python -m eval.report              # assemble from eval/results/*
    uv run python -m eval.report --print      # ... and print the markdown
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
QUERIES_PATH = EVAL_DIR / "queries.jsonl"

# suite -> regex over the results file name (the trailing group keeps the
# plain retrieval name from swallowing the `_rag`/`_clustering` files).
SUITE_PATTERNS: dict[str, str] = {
    "retrieval": r"^\d{8}_[0-9a-f]{7}\.json$",
    "rag": r"^\d{8}_[0-9a-f]{7}_rag\.json$",
    "clustering": r"^\d{8}_[0-9a-f]{7}_clustering\.json$",
    "quality": r"^\d{8}_[0-9a-f]{7}_quality\.json$",
}
SUITE_ORDER = ("retrieval", "rag", "clustering", "quality")


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=EVAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _display_path(path: Path) -> str:
    """Repo-relative when the file lives in the repo, absolute otherwise."""
    try:
        return path.resolve().relative_to(EVAL_DIR.parent.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_suite_files(results_dir: Path = RESULTS_DIR) -> dict[str, Path]:
    """Newest results file per suite, by modification time."""
    found: dict[str, Path] = {}
    for suite, pattern in SUITE_PATTERNS.items():
        rx = re.compile(pattern)
        candidates = [path for path in results_dir.glob("*.json") if rx.match(path.name)]
        if candidates:
            found[suite] = max(candidates, key=lambda path: path.stat().st_mtime)
    return found


def merge_with_existing(
    payloads: dict[str, dict[str, Any]], results_dir: Path = RESULTS_DIR
) -> dict[str, dict[str, Any]]:
    """Suites from this run override the newest on-disk file for the others.

    A single-suite run (`--suite rag`) still produces a complete baseline: the
    suite that just ran wins, the rest come from the results directory.
    """
    merged: dict[str, dict[str, Any]] = {}
    for suite, path in latest_suite_files(results_dir).items():
        merged[suite] = json.loads(path.read_text(encoding="utf-8"))
    merged.update(payloads)
    return merged


def build_baseline(
    payloads: dict[str, dict[str, Any]],
    queries_path: Path = QUERIES_PATH,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """One baseline document from the suites' own payloads (unmodified)."""
    snapshot = snapshot_id
    if snapshot is None:
        candidates = [payload.get("snapshot_id") for payload in payloads.values()]
        snapshot = next((value for value in candidates if value), None)
    return {
        "suite": "baseline",
        "snapshot_id": snapshot,
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "queries_file": _display_path(queries_path),
        "queries_sha256": _sha256(queries_path) if queries_path.exists() else None,
        "suites_present": [suite for suite in SUITE_ORDER if suite in payloads],
        "suite_git_shas": {
            suite: payload.get("git_sha") for suite, payload in sorted(payloads.items())
        },
        "suites": {suite: payloads[suite] for suite in SUITE_ORDER if suite in payloads},
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _retrieval_section(payload: dict[str, Any]) -> list[str]:
    per_config = payload.get("per_config") or {}
    pool = payload.get("pool") or {}
    lines = [
        "## Retrieval ablation (macro-averaged, snapshot pool grades)",
        "",
        f"Queries: {payload.get('n_queries')} · pool depth {pool.get('depth')} · mean pool "
        f"size {_fmt(pool.get('mean_pool_size'))} (median {_fmt(pool.get('median_pool_size'))}) "
        f"· unjudged chunks count as 0.",
        "",
        "| config | recall@5 | recall@10 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|---|",
    ]
    chunk_level = [key for key in sorted(per_config) if not key.endswith("__article_level")]
    article_level = [key for key in sorted(per_config) if key.endswith("__article_level")]
    for config_id in chunk_level:
        agg = per_config[config_id]
        lines.append(
            f"| {config_id} | {_fmt(agg.get('recall@5'))} | {_fmt(agg.get('recall@10'))} "
            f"| {_fmt(agg.get('mrr@10'))} | {_fmt(agg.get('ndcg@10'))} |"
        )
    if article_level:
        lines += [
            "",
            "Article-level projection of the same rankings (any grade-2 chunk in the "
            "article counts as a hit; the unit the RAG path actually cites):",
            "",
            "| config | recall@5 | recall@10 | MRR@10 | nDCG@10 |",
            "|---|---|---|---|---|",
        ]
        for config_id in article_level:
            agg = per_config[config_id]
            lines.append(
                f"| {config_id.removesuffix('__article_level')} "
                f"| {_fmt(agg.get('recall@5'))} | {_fmt(agg.get('recall@10'))} "
                f"| {_fmt(agg.get('mrr@10'))} | {_fmt(agg.get('ndcg@10'))} |"
            )
    return lines + [""]


def _rag_section(payload: dict[str, Any]) -> list[str]:
    aggregates = payload.get("aggregates") or {}
    fast = aggregates.get("fast") or {}
    deep = aggregates.get("deep") or {}
    subq = payload.get("sub_question_relevance") or {}
    judge = payload.get("judge") or {}
    fallback = ""
    if judge.get("fallback_used"):
        reason = judge.get("fallback_reason")
        fallback = f"; fell back ({reason})" if reason else "; fell back"
    lines = [
        "## RAG fast vs deep",
        "",
        f"Judge: `{judge.get('version')}` requested `{judge.get('model')}`{fallback} "
        f"(errors: {judge.get('errors')}; cached judgments "
        f"{judge.get('cached_judgments')}) · refusal detection is deterministic regex "
        f"first, judge only for borderline cases.",
        "",
        "| metric | fast | deep |",
        "|---|---|---|",
    ]
    for key in (
        "citation_precision",
        "citation_coverage",
        "required_fact_recall",
        "invalid_citation_rate",
        "refusal_rate",
        "refusal_when_evidence_exists_rate",
        "refusal_without_evidence_rate",
        "judge_routed_count",
    ):
        lines.append(f"| {key} | {_fmt(fast.get(key))} | {_fmt(deep.get(key))} |")
    lines.append(
        f"| sub_question_relevance | — | {_fmt(subq.get('mean'))} "
        f"(n={subq.get('judged')}, err={subq.get('errors')}) |"
    )
    lines += ["", "| path | cold (ms) | warm p50 (ms) | warm p95 (ms) |", "|---|---|---|---|"]
    for path in ("fast", "deep"):
        latency = (aggregates.get(path) or {}).get("latency_ms") or {}
        lines.append(
            f"| {path} | {_fmt(latency.get('cold'))} | {_fmt(latency.get('warm_p50'))} "
            f"| {_fmt(latency.get('warm_p95'))} |"
        )
    return lines + [""]


def _clustering_section(payload: dict[str, Any]) -> list[str]:
    slow = payload.get("slow_path") or {}
    fast = payload.get("fast_path") or {}
    combined = payload.get("combined_partition") or {}
    lines = [
        "## Clustering vs the labelled event set",
        "",
        f"Label set: {payload.get('label_set', {}).get('file')} "
        f"({payload.get('label_set', {}).get('status')}) — "
        f"{payload.get('label_set', {}).get('n_articles')} articles in "
        f"{payload.get('label_set', {}).get('n_events')} true events "
        f"({payload.get('label_set', {}).get('cross_source_events')} cross-source).",
        "",
        "| metric | value |",
        "|---|---|",
        f"| slow-path input (unmatched articles) | {slow.get('n_input_articles')} |",
        f"| slow-path ARI / NMI / purity (labelled members) | {_fmt(slow.get('ari'))} / "
        f"{_fmt(slow.get('nmi'))} / {_fmt(slow.get('purity'))} |",
        f"| combined ARI / NMI / purity | {_fmt(combined.get('ari'))} / "
        f"{_fmt(combined.get('nmi'))} / {_fmt(combined.get('purity'))} |",
        f"| fast-path centroid precision (threshold {_fmt(fast.get('threshold'))}) "
        f"| {_fmt(fast.get('precision'))} ({fast.get('judgeable_correct')}/"
        f"{fast.get('judgeable_matches')}) |",
    ]
    if slow.get("note"):
        lines.append(f"| note | {slow['note']} |")
    return lines + [""]


def _quality_section(payload: dict[str, Any]) -> list[str]:
    chunk = payload.get("chunk_level") or {}
    article = payload.get("article_level") or {}
    held = payload.get("heldout") or {}
    labels = payload.get("labels") or {}

    def ci(values: Any) -> str:
        return f"{values[0]:.3f}-{values[1]:.3f}" if values else "—"

    def precision(value: Any) -> str:
        """No positive predictions means undefined, which is not 0.000."""
        return "undefined" if value is None else _fmt(value)

    trigger = payload.get("trigger") or {}
    trigger_line = (
        "Threshold trigger (precision < 0.95): "
        f"**{'FIRED' if trigger.get('fired') else 'not fired'}**"
    )
    if trigger.get("note"):
        trigger_line += f" — {trigger['note']}"
    return [
        "## Content quality (held-out, freshly ingested chunks)",
        "",
        f"Held-out set: {held.get('n_chunks')} chunks from a new ingest "
        f"(TechCrunch {held.get('stratification', {}).get('techcrunch')}, "
        f"BBC {held.get('stratification', {}).get('bbc')}); labels: "
        f"{labels.get('status')} (self-consistency disagreements "
        f"{labels.get('self_consistency_disagreements')}).",
        "",
        "| level | precision (95% CI) | recall (95% CI) | TP/FP/FN |",
        "|---|---|---|---|",
        f"| chunk | {precision(chunk.get('precision'))} ({ci(chunk.get('precision_ci95'))}) "
        f"| {_fmt(chunk.get('recall'))} ({ci(chunk.get('recall_ci95'))}) "
        f"| {chunk.get('tp')}/{chunk.get('fp')}/{chunk.get('fn')} |",
        f"| article | {precision(article.get('precision'))} ({ci(article.get('precision_ci95'))}) "
        f"| {_fmt(article.get('recall'))} ({ci(article.get('recall_ci95'))}) "
        f"| {article.get('tp')}/{article.get('fp')}/{article.get('fn')} |",
        "",
        trigger_line + ".",
        "",
    ]


_SECTION_RENDERERS = {
    "retrieval": _retrieval_section,
    "rag": _rag_section,
    "clustering": _clustering_section,
    "quality": _quality_section,
}


def render_md(baseline: dict[str, Any]) -> str:
    """The human-readable baseline: one table per suite, plus provenance."""
    lines = [
        f"# Phase 2 evaluation baseline — snapshot `{baseline['snapshot_id']}`",
        "",
        f"Assembled {baseline['created_at']} at `{baseline['git_sha']}` — "
        f"queries `{baseline.get('queries_file')}` "
        f"sha256 `{str(baseline.get('queries_sha256'))[:12]}…`.",
        "",
        "No quality target is set in Phase 2: these are the numbers later phases "
        "are graded against. Unjudged chunks count as 0 everywhere; every metric "
        "with an undefined denominator is excluded from its macro-average instead "
        "of being coerced to 0.",
        "",
        "Suites present: " + ", ".join(f"`{suite}`" for suite in baseline["suites_present"]),
        "",
    ]
    for suite in baseline["suites_present"]:
        payload = baseline["suites"][suite]
        lines.append(
            f"_(suite git sha: `{payload.get('git_sha')}`, mock={payload.get('mock', False)})_"
        )
        lines.append("")
        lines.extend(_SECTION_RENDERERS[suite](payload))
    return "\n".join(lines).rstrip() + "\n"


def write_baseline(
    payloads: dict[str, dict[str, Any]],
    results_dir: Path = RESULTS_DIR,
    queries_path: Path = QUERIES_PATH,
) -> tuple[Path, Path]:
    """Write the baseline json/md and point ``latest.json`` at it."""
    baseline = build_baseline(payloads, queries_path=queries_path)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    name = f"{stamp}_{baseline['git_sha']}_baseline"
    json_path = results_dir / f"{name}.json"
    md_path = results_dir / f"{name}.md"
    json_path.write_text(json.dumps(baseline, indent=1), encoding="utf-8")
    md_path.write_text(render_md(baseline), encoding="utf-8")
    (results_dir / "latest.json").write_text(json.dumps(baseline, indent=1), encoding="utf-8")
    latest_path = results_dir / "latest.json"
    print(f"baseline    : {json_path}\n              {md_path}\nlatest      : {latest_path}")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", dest="print_md", action="store_true")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)
    results_dir = Path(args.results_dir)
    files = latest_suite_files(results_dir)
    if not files:
        raise SystemExit(f"no suite results in {results_dir}")
    payloads = {
        suite: json.loads(path.read_text(encoding="utf-8")) for suite, path in files.items()
    }
    for suite, path in sorted(files.items()):
        print(f"using {suite:11s}: {path.name}")
    _json_path, md_path = write_baseline(payloads, results_dir=results_dir)
    if args.print_md:
        print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
