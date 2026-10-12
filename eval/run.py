"""Unified eval runner (plan §4).

    uv run python -m eval.run --suite retrieval
    uv run python -m eval.run --suite rag
    uv run python -m eval.run --suite clustering
    uv run python -m eval.run --suite quality
    uv run python -m eval.run --suite all
    uv run python -m eval.run --suite ci        # mocked CI subset (--mock implied)

Flags: --snapshot <id>, --queries <path> (informational; the suites read
eval/queries.jsonl), --limit N, --mock (no Ollama/Qdrant network calls),
--baseline <path> (compare against a committed results file: exact equality
for --mock, report-only for real runs).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent


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


def run_suite(suite: str, snapshot_id: str, limit: int | None, mock: bool) -> dict[str, Any]:
    if suite == "retrieval":
        from eval import retrieval_suite

        if mock:
            raise SystemExit("retrieval suite has no --mock mode; use --suite ci")
        retrieval_suite.collect(snapshot_id, limit=limit)
        retrieval_suite.grade(snapshot_id)
        json_path, _md_path = retrieval_suite.score(snapshot_id)
        return json.loads(json_path.read_text(encoding="utf-8"))
    if suite == "rag":
        from eval import rag_suite

        rag_suite.run(snapshot_id, limit=limit, mock=mock)
        json_path, _md_path = rag_suite.score(snapshot_id, mock=mock)
        return json.loads(json_path.read_text(encoding="utf-8"))
    if suite == "clustering":
        from eval import clustering_suite

        return clustering_suite.run(snapshot_id, limit=limit)
    if suite == "quality":
        from eval import quality_suite

        return quality_suite.run(limit=limit)
    if suite == "ci":
        return run_ci_subset(snapshot_id, limit)
    raise SystemExit(f"unknown suite: {suite}")


def run_ci_subset(snapshot_id: str, limit: int | None = None) -> dict[str, Any]:
    """The mocked 12-query subset: harness arithmetic only, no quality."""
    from eval import ci_subset

    return ci_subset.run(limit=limit)


def compare_baseline(results: dict[str, Any], baseline_path: Path, exact: bool) -> bool:
    """Compare a run against a committed baseline. Exact equality when mocked."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if exact:
        if results == baseline:
            print("baseline check: EXACT match")
            return True
        print("baseline check: MISMATCH — the harness changed, not quality")
        _print_diff(results, baseline)
        return False
    print("baseline check: report-only (real run; quality targets come later)")
    return True


def _print_diff(results: Any, baseline: Any, path: str = "") -> None:
    if isinstance(results, dict) and isinstance(baseline, dict):
        for key in sorted(set(results) | set(baseline)):
            _print_diff(results.get(key), baseline.get(key), f"{path}.{key}")
    elif isinstance(results, list) and isinstance(baseline, list):
        if len(results) != len(baseline):
            print(f"  {path}: length {len(results)} != {len(baseline)}")
        else:
            for i, (a, b) in enumerate(zip(results, baseline, strict=False)):
                _print_diff(a, b, f"{path}[{i}]")
    elif results != baseline:
        print(f"  {path}: {results!r} != {baseline!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        required=True,
        choices=["retrieval", "rag", "clustering", "quality", "all", "ci"],
    )
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--queries", default="eval/queries.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--baseline", default=None, help="compare results against this file")
    args = parser.parse_args(argv)
    snapshot_id = args.snapshot or _latest_snapshot_id()

    suites = ["retrieval", "rag", "clustering", "quality"] if args.suite == "all" else [args.suite]
    combined: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "git_sha": _git_sha(),
        "mock": args.mock or args.suite == "ci",
        "suites": {},
    }
    for suite in suites:
        print(f"=== suite: {suite} ===")
        combined["suites"][suite] = run_suite(suite, snapshot_id, args.limit, args.mock)

    if args.baseline:
        exact = combined["mock"]
        if not compare_baseline(combined, Path(args.baseline), exact=exact):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
