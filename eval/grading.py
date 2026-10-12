"""Deterministic draft-grading rule (plan §1.3; agent-drafted, human-reviewed).

For a pooled chunk of one query:

* grade 2 — the chunk text contains a required fact (directly answers);
* grade 1 — the chunk belongs to an article the labeler picked by reading
  (topically related) but contains no required fact;
* grade 0 — otherwise.

Reading-based corrections live in ``eval/label_overrides.jsonl`` and always
win over the rule (each carries a reason, visible in review).
"""

from __future__ import annotations


def draft_grade(
    text: str,
    required_facts: list[str],
    *,
    is_labeler_pick_chunk: bool,
    is_labeler_pick_article: bool,
) -> tuple[int, str]:
    """Return ``(grade, source)`` for one pooled chunk."""
    lowered = (text or "").lower()
    if any(fact.lower() in lowered for fact in required_facts):
        return 2, "rule_fact"
    if is_labeler_pick_chunk or is_labeler_pick_article:
        return 1, "rule_pick"
    return 0, "rule_zero"
