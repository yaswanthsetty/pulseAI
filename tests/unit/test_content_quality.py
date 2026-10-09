"""Classifier regression gate against the hand-labelled fixture.

The fixture (80 live-corpus chunks labelled junk/clean by hand, stratified to
include every scan-flagged chunk and the 0.40-0.60 score band) is the contract
for any threshold/weight change:

* **precision >= 0.95** - deleting real body text is worse than keeping junk.
* **recall >= 0.90** - junk must not survive to embedding.
"""

import json
from pathlib import Path

from backend.core import content_quality as cq

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "boilerplate_labels.jsonl"


def _load():
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]
    return rows


def test_fixture_present_and_stratified():
    rows = _load()
    # 80 stratified from the live scan + 22 hard cases appended after
    # hand-reviewing every flag raised on the real corpus (guardian/verge
    # prose, bbc listings) - see docs/evidence/phase1_smoke.md.
    assert len(rows) == 102, "fixture must keep the full labelled sample"
    assert len({r["cid"] for r in rows}) == 102, "fixture rows must be unique chunks"
    junk = sum(1 for r in rows if r["label"] == "junk")
    assert junk == 65 and len(rows) - junk == 37, "labels must not drift silently"


def test_precision_and_recall_on_fixture():
    rows = _load()
    tp = fp = fn = tn = 0
    for row in rows:
        predicted = cq.is_boilerplate(row["text"])
        actual = row["label"] == "junk"
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    failures = []
    if precision < 0.95:
        failures.append(f"precision {precision:.3f} < 0.95 (FP={fp})")
    if recall < 0.90:
        failures.append(f"recall {recall:.3f} < 0.90 (FN={fn})")
    assert not failures, "; ".join(failures)


def test_strong_marker_rejects_even_without_repetition():
    # TechCrunch Disrupt promo block, single occurrence - caught by marker alone.
    text = (
        "Topics Scale faster. Grow your portfolio. Gain practical expertise. "
        "No matter your goal, Disrupt can empower you. Save up to $300 toda y! "
        "Newsletters Subscribe for the industry's biggest tech news Related AI"
    )
    assert cq.is_boilerplate(text)


def test_mixed_body_with_nav_tail_is_not_rejected():
    # Real prose first, nav tail after (idx 4 of the fixture) -> keep, to protect body text.
    rows = {r["cid"]: r for r in _load()}
    mixed = next(
        r for r in rows.values() if r["label"] == "clean" and "Grow your portfolio" in r["text"]
    )
    assert not cq.is_boilerplate(mixed["text"])


def test_body_is_usable_length_and_quality_gates():
    assert not cq.body_is_usable("too short")
    long_nav = "Most Popular " + "Stripe will reportedly acquire AI gateway startup " * 40
    assert not cq.body_is_usable(long_nav)
    real_body = (
        "The world is familiar by now with the usual tropes of machine-generated text: "
        "overuse of the word delve, an excess of em dashes, and the chirpy, relentless "
        "construction of its not X but Y. But is it about to get even worse? Anthropic "
        "says it will change the way its chatbot makes small, random choices, to comply "
        "with EU regulation that requires AI-generated text to be watermarked. "
        "Researchers caution that detection remains unsolved in practice, and that "
        "watermarks can be stripped by paraphrasing or by simple translation tools."
    )
    assert cq.body_is_usable(real_body)


def test_count_rejected_splits_pieces():
    junk = "Newsletters Subscribe for the industry's biggest tech news Related AI " * 20
    good = (
        "Regulators are asking labs to document how training data was licensed. "
        "The industry argues that disclosure requirements slow releases without "
        "preventing harm. Meanwhile courts are testing whether model outputs "
        "inherit copyright from their inputs. Several companies have published "
        "voluntary commitments, though enforcement remains unspecified and no "
        "independent auditor has yet been accredited to verify compliance."
    )
    kept, rejected = cq.count_rejected([good, junk, junk])
    assert kept == [good]
    assert rejected == 2
