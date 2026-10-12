"""Human agreement check for the LLM judge (docs/plans/phase2_eval.md §3).

The judge is only trustworthy if a human agrees with it: **20% of LLM-judged
items** are also labelled by hand, and the raw agreement and Cohen's κ are
reported. **κ ≥ 0.70 is the gate** — below it the rubric is revised and the
sample re-labelled; the numbers are never reported as if the gate passed.

Two phases, deliberately separated so the human never sees the judge's score
while labelling:

    uv run python -m eval.judge_agreement export    # blinded sample to label
    uv run python -m eval.judge_agreement report    # raw agreement + kappa

``export`` reads the judged items out of the RAG results file
(``eval/results/*_rag.json``), samples 20% deterministically (seeded by the
query id of each item, so the sample is reproducible and reviewable in git),
and writes ``eval/judge_agreement.jsonl`` with ``human_score: null``. The
labeller fills ``human_score`` with 0/1/2 per item — the file is committed with
both scores, which is the audit trail for the κ number.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
SAMPLE_PATH = EVAL_DIR / "judge_agreement.jsonl"
KAPPA_GATE = 0.70
DEFAULT_PERCENT = 20


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


def _item_key(item: dict[str, Any], index: int) -> str:
    return f"{item['query_id']}|{item['path']}|{item['task']}|{index}"


def collect_items(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Every scored LLM judgment in a RAG results file (judge errors excluded)."""
    items: list[dict[str, Any]] = []
    for index, item in enumerate(results.get("judge_items", [])):
        if item.get("score") is None:
            continue  # a judge error is not a judgment to agree with
        items.append({**item, "key": _item_key(item, index)})
    return items


def sample_items(
    items: list[dict[str, Any]], percent: int = DEFAULT_PERCENT
) -> list[dict[str, Any]]:
    """Deterministic 20% sample: hash-ranked keys, so the same set every run."""
    if not items:
        return []
    want = max(1, math.ceil(len(items) * percent / 100))
    ranked = sorted(items, key=lambda item: hashlib.sha256(item["key"].encode()).hexdigest())
    return sorted(ranked[:want], key=lambda item: item["key"])


def export(results_path: Path, percent: int = DEFAULT_PERCENT) -> Path:
    """Write the blinded human-labelling sample (judge scores hidden from view)."""
    results = json.loads(results_path.read_text(encoding="utf-8"))
    items = collect_items(results)
    if not items:
        raise SystemExit(f"no scored judge items in {results_path} — run the RAG suite first")
    sample = sample_items(items, percent)
    rows = [
        {
            "id": f"j{index:03d}",
            "key": item["key"],
            "query_id": item["query_id"],
            "path": item["path"],
            "task": item["task"],
            "items": item["items"],
            # Filled by the human labeller; the judge's own score is kept in a
            # separate field so the label can be recorded before it is revealed.
            "human_score": None,
            "human_notes": "",
            "judge_score": item["score"],
            "judge_model": item["model"],
            "judge_reason": item["reason"],
        }
        for index, item in enumerate(sample)
    ]
    SAMPLE_PATH.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    print(
        f"sample  : {SAMPLE_PATH} ({len(rows)} of {len(items)} judged items, "
        f"{percent}%, seeded by item key)\n"
        "next    : label each row's human_score with 0/1/2 (do not look at "
        "judge_score first, then compare) and run the report phase"
    )
    return SAMPLE_PATH


def load_sample(path: Path = SAMPLE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"no agreement sample at {path} — run the export phase first")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def report(path: Path = SAMPLE_PATH) -> dict[str, Any]:
    """Raw agreement + Cohen's κ, overall and per judged task."""
    from eval.judge import build_judge

    rows = load_sample(path)
    pending = [row["id"] for row in rows if row.get("human_score") is None]
    if pending:
        raise SystemExit(
            f"{len(pending)}/{len(rows)} items still unlabelled "
            f"(human_score is null): {', '.join(pending[:8])}" + (" …" if len(pending) > 8 else "")
        )
    invalid = [row["id"] for row in rows if row.get("human_score") not in (0, 1, 2)]
    if invalid:
        raise SystemExit(f"human_score must be 0/1/2 — offending rows: {', '.join(invalid)}")

    judge = build_judge()
    overall = judge.agreement([({"score": row["judge_score"]}, row["human_score"]) for row in rows])
    by_task: dict[str, dict[str, float]] = {}
    for task in sorted({row["task"] for row in rows}):
        subset = [row for row in rows if row["task"] == task]
        by_task[task] = judge.agreement(
            [({"score": row["judge_score"]}, row["human_score"]) for row in subset]
        )

    results = {
        "suite": "judge_agreement",
        "git_sha": _git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "sample_file": _display_path(path),
        "judge": judge.version,
        "n_items": len(rows),
        "percent_of_judged_items": DEFAULT_PERCENT,
        "overall": overall,
        "by_task": by_task,
        "gate": {
            "rule": f"kappa >= {KAPPA_GATE} to trust the judge",
            "passed": overall["kappa"] >= KAPPA_GATE,
            "on_failure": "revise the frozen rubric (new judge version) and re-label the sample",
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    name = f"{stamp}_{results['git_sha']}_judge_agreement"
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    md = "\n".join(
        [
            f"# Judge human-agreement check — {results['git_sha']}",
            "",
            f"{results['n_items']} items ({results['percent_of_judged_items']}% of "
            f"LLM-judged items) hand-labelled; judge `{results['judge']}`.",
            "",
            "| scope | n | raw agreement | Cohen's κ |",
            "|---|---|---|---|",
            f"| overall | {overall['n']} | {overall['raw_agreement']:.3f} "
            f"| {overall['kappa']:.3f} |",
        ]
        + [
            f"| {task} | {stats['n']} | {stats['raw_agreement']:.3f} | {stats['kappa']:.3f} |"
            for task, stats in sorted(by_task.items())
        ]
        + [
            "",
            f"Gate (κ ≥ {KAPPA_GATE}): **{'PASSED' if results['gate']['passed'] else 'FAILED'}**"
            + ("" if results["gate"]["passed"] else " — revise the rubric, then re-label."),
            "",
        ]
    )
    (RESULTS_DIR / f"{name}.md").write_text(md, encoding="utf-8")
    print(md)
    return results


def latest_rag_results() -> Path:
    files = sorted(RESULTS_DIR.glob("*_rag.json"))
    if not files:
        raise SystemExit("no *_rag.json in eval/results — run the RAG suite first")
    return files[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["export", "report"])
    parser.add_argument("--results", default=None, help="RAG results file (export phase)")
    parser.add_argument("--sample", default=str(SAMPLE_PATH))
    parser.add_argument("--percent", type=int, default=DEFAULT_PERCENT)
    args = parser.parse_args(argv)
    if args.phase == "export":
        export(Path(args.results) if args.results else latest_rag_results(), percent=args.percent)
    else:
        report(Path(args.sample))
    return 0


if __name__ == "__main__":
    sys.exit(main())
