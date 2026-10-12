"""Deterministic refusal/hedge detection for the RAG suite.

The regex list in ``eval/refusal_patterns.txt`` is committed before the first
scored run (docs/plans/phase2_eval.md §2.2). Matching is case-insensitive;
the LLM judge is consulted only for borderline cases and every judge-routed
case is counted in the results file.

Routing rule (deterministic, no LLM unless borderline):

* a pattern match            -> refusal (never routed to the judge)
* no match + a required fact present in the answer -> not a refusal
* no match + no required fact + a grade-2 chunk was retrieved
  -> **borderline**: routed to the LLM judge, counted in results
* otherwise (no match, no fact, no grade-2 evidence) -> not counted as
  refusal-when-evidence-exists by construction (the metric requires
  retrieved evidence); still recorded as a plain non-match
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PATTERNS_PATH = Path(__file__).resolve().parent / "refusal_patterns.txt"


@lru_cache(maxsize=1)
def load_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Compile the committed regex list (comments and blanks skipped)."""
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for raw_line in Path(PATTERNS_PATH).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        compiled.append((line, re.compile(line, re.IGNORECASE)))
    return tuple(compiled)


def match_refusal(answer: str) -> list[str]:
    """The committed patterns that match ``answer`` (empty = no refusal)."""
    text = answer or ""
    return [source for source, pattern in load_patterns() if pattern.search(text)]


def contains_required_fact(answer: str, required_facts: list[str]) -> bool:
    """True when at least one required fact is a literal substring of the answer."""
    lowered = (answer or "").lower()
    return any(fact.lower() in lowered for fact in required_facts)


def classify(
    answer: str,
    *,
    required_facts: list[str],
    grade2_retrieved: bool,
) -> str:
    """Refusal classification for one answer.

    Returns ``"refusal"`` (pattern match), ``"ok"`` (fact present or no
    evidence was retrieved), or ``"borderline"`` (must go to the judge).
    """
    if match_refusal(answer):
        return "refusal"
    if contains_required_fact(answer, required_facts):
        return "ok"
    if grade2_retrieved:
        return "borderline"
    return "ok"
