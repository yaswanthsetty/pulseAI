"""Unit tests for eval.refusal — deterministic refusal detection + routing."""

from __future__ import annotations

from eval.refusal import classify, contains_required_fact, load_patterns, match_refusal


def test_patterns_file_is_committed_and_nonempty():
    patterns = load_patterns()
    assert len(patterns) >= 10  # a real list, not a stub
    assert all(p.search for _src, p in patterns)


def test_explicit_refusal_matches():
    assert match_refusal("I don't have information about that in the provided sources.")
    assert match_refusal("I'm sorry, but I cannot answer this question.")
    assert match_refusal("The provided context does not mention the price.")


def test_evidence_absence_matches():
    assert match_refusal("There is no information about battery life in the sources.")
    assert match_refusal("This was not stated in the documents.")
    assert match_refusal("The model doesn't specify a release date.")
    assert match_refusal("I could not find that detail in the text.")


def test_normal_grounded_answer_does_not_match():
    answer = (
        "The Nothing Phone 4a Pro has a 6.83in OLED display with a 120Hz refresh "
        "rate, according to the Guardian review [#1]. It may ship in mid-2026 [#2]."
    )
    assert match_refusal(answer) == []


def test_hedge_language_alone_is_not_a_refusal():
    # Conservative list: ordinary uncertainty in quoted reporting is not a refusal.
    assert match_refusal("The company said the rollout might begin in June.") == []


def test_case_insensitive():
    assert match_refusal("THE PROVIDED SOURCES DO NOT CONTAIN THIS INFORMATION.")


def test_contains_required_fact_case_insensitive():
    assert contains_required_fact("It costs $499 in the US.", ["$499"])
    assert contains_required_fact("It costs $499 in the US.", ["499"])
    assert not contains_required_fact("It costs $499.", ["€499"])


def test_classify_refusal_pattern_wins():
    verdict = classify(
        "I don't have information about that.",
        required_facts=["6.83in OLED"],
        grade2_retrieved=True,
    )
    assert verdict == "refusal"


def test_classify_ok_when_fact_present():
    verdict = classify(
        "The screen is a 6.83in OLED panel.",
        required_facts=["6.83in OLED"],
        grade2_retrieved=True,
    )
    assert verdict == "ok"


def test_classify_borderline_when_no_fact_but_evidence_existed():
    verdict = classify(
        "The phone is a mid-range device with a aluminium frame.",
        required_facts=["6.83in OLED"],
        grade2_retrieved=True,
    )
    assert verdict == "borderline"


def test_classify_ok_when_nothing_was_retrieved():
    verdict = classify(
        "Unclear.",
        required_facts=["6.83in OLED"],
        grade2_retrieved=False,
    )
    assert verdict == "ok"


def test_classify_handles_empty_answer():
    assert classify("", required_facts=["x"], grade2_retrieved=True) == "borderline"
    assert match_refusal("") == []
