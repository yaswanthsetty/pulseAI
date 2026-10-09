"""Content-quality signals shared by ingestion and retrieval.

Two consumers, one implementation (business modules are independent, so the
classifier lives in ``core``):

* **ingestion** - the ingest-time quality gate decides whether extracted text
  is usable article body or should fall back to the feed summary.
* **retrieval** - the pre-embed chunk filter drops boilerplate chunks so junk
  never becomes a vector.

The classifier is calibrated against ``tests/fixtures/boilerplate_labels.jsonl``
(hand-labelled chunks from the live corpus); ``tests/unit/
test_content_quality.py`` fails if precision drops below 0.95 or recall below
0.90. Threshold/weight changes must be measured against that fixture, not
guessed. Current measurement on the fixture: precision 1.00, recall 1.00.

Design notes
------------
* Precision is favored: a chunk is only called boilerplate when either
  (a) a strong marker phrase appears in a *non-prose prefix* (nav rails,
  promo blocks, author-bio blocks, programme listings - these never lead
  real article prose), or (b) the composite repetition/phrase score crosses
  the threshold.
* A mixed chunk (real prose first, nav tail after) is protected by the prose
  guard: losing real body text is worse than keeping some junk.
"""

import re
from collections import Counter

# Bump when extraction/quality logic changes semantics, so re-extraction jobs
# can target only articles processed by an older version.
EXTRACTOR_VERSION = "1"

#: Extracted bodies shorter than this fall back to the feed summary.
MIN_BODY_CHARS = 300

#: Composite score at/above which a chunk is boilerplate.
BOILERPLATE_THRESHOLD = 0.45

#: Whole-body quality gate: text is unusable when this fraction of its
#: fixed-size windows scores as boilerplate (pure chrome/listing extraction).
_GATE_WINDOW_CHARS = 600
_GATE_JUNK_FRACTION = 0.8

#: Added to the score when a strong marker sits in a non-prose prefix
#: (i.e. the chunk opens with chrome rather than prose).
STRONG_MARKER_PENALTY = 0.45

# Weak markers: contribute to the composite phrase-density component. They may
# legitimately appear inside article prose, so they never reject alone.
PHRASES = [
    "most popular",
    "newsletter",
    "subscribe",
    "sign up",
    "read more",
    "trending",
    "watch more",
    "share this",
    "advertisement",
    "continue reading",
    "more from",
    "all rights reserved",
    "cookie",
    "privacy policy",
    "terms of use",
    "download the",
    "get the latest",
    "also read",
    "related ai",
    "sponsored",
]

# Strong markers: site chrome that never opens real article prose. The prose
# guard (below) protects mixed chunks where substantial body text precedes the
# marker.
STRONG_MARKERS = [
    "grow your portfolio",  # TechCrunch Disrupt promo block
    "gain practical expertise",
    "save up to $300",
    "when you purchase through links in our articles",  # affiliate disclosure
    "you can contact or verify outreach from",  # author-bio block
    "newsletters subscribe",  # TechCrunch newsletter rail
    "biggest tech news",
    "most popular",  # TechCrunch related-story rail
    "related ai",
    "latest in ai",  # TechCrunch "In Brief" rail
    "close panel",  # BBC follow-widget chrome
    "sign up here",  # BBC newsletter CTA
    "more on this story",  # BBC related-links rail
]

#: BBC programme-listing pattern ("24 mins", "25 mins") - index pages, not
#: prose. A listing is a listing even when an introductory sentence precedes
#: it, so this marker bypasses the prose guard.
_DURATION_LISTING_RE = re.compile(r"\b\d{2} mins\b")

# Weights calibrated on the labelled fixture (grid search under the
# precision >= 0.95 constraint, expanded after hand-reviewing every flag the
# classifier raised on the live corpus): strong-marker penalty + a verbatim
# span repeated >=3x (related-story cards) carry the decision; token
# repetition and phrase density only contribute. Sum <= 1.0.
_STRONG_WEIGHT = 0.45
_SPAN_WEIGHT = 0.35
_REP_TOK_WEIGHT = 0.10
_PHRASE_WEIGHT = 0.10


def _norm_tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()


def _repetition(tokens: list[str]) -> tuple[float, bool]:
    """(token-duplication normalized to 0..1, verbatim span repeated >=3x).

    Normal English prose reuses ~35-45% of its tokens, so token duplication
    alone is a weak signal and carries little weight. The >=3x span flag is
    the strong one: only related-story rails and card lists repeat a whole
    5-word span three or more times; prose, spec sheets and transcripts top
    out at 2 (a single duplicated line).
    """
    if not tokens:
        return 0.0, False
    dup_tok = 1.0 - (len(set(tokens)) / len(tokens))
    shingles = [tuple(tokens[i : i + 5]) for i in range(len(tokens) - 4)]
    if not shingles:
        return min(dup_tok / 0.55, 1.0), False
    max_repeats = max(Counter(shingles).values())
    return min(dup_tok / 0.55, 1.0), max_repeats >= 3


def _phrase_density(text_lower: str, word_count: int) -> float:
    hits = sum(1 for phrase in PHRASES if phrase in text_lower)
    if word_count <= 0:
        return 0.0
    return min((hits * 100.0 / word_count) / 6.0, 1.0)


def _first_strong_marker_offset(text_lower: str) -> int | None:
    offsets = [text_lower.find(m) for m in STRONG_MARKERS]
    offsets = [o for o in offsets if o >= 0]
    match = _DURATION_LISTING_RE.search(text_lower)
    if match and text_lower.count(" mins") >= 2:
        offsets.append(match.start())
    return min(offsets) if offsets else None


def _duration_listing(text_lower: str) -> bool:
    """True for programme-index pages ("24 mins", "25 mins", ...)."""
    match = _DURATION_LISTING_RE.search(text_lower)
    return bool(match and text_lower.count(" mins") >= 2)


def _looks_like_prose(prefix: str) -> bool:
    """True when ``prefix`` is substantial, varied article prose.

    Guards mixed chunks (body first, nav tail after) from the strong-marker
    penalty. Counts ``.!?`` anywhere (BBC quotes end with smart quotes, not
    ``. ``); repetitive chrome (author bios repeating a contact line) fails
    the distinctness test and is not prose.
    """
    sentences = len(re.findall(r"[.!?]", prefix))
    tokens = _norm_tokens(prefix)
    if not tokens:
        return False
    distinct = len(set(tokens))
    if sentences < 1 or distinct < 20:
        return False
    return distinct / len(tokens) >= 0.60


def boilerplate_score(text: str) -> float:
    """Composite 0..1 boilerplate score for ``text``."""
    if not text or not text.strip():
        return 0.0
    tokens = _norm_tokens(text)
    text_lower = text.lower()
    rep_tok, span_repeated = _repetition(tokens)
    score = _REP_TOK_WEIGHT * rep_tok
    if span_repeated:
        score += _SPAN_WEIGHT
    score += _PHRASE_WEIGHT * _phrase_density(text_lower, len(tokens))
    # Duration listings are index pages whatever prose precedes them; other
    # strong markers only reject when the chunk does not *open* with prose.
    if _duration_listing(text_lower):
        score += STRONG_MARKER_PENALTY
    else:
        offset = _first_strong_marker_offset(text_lower)
        if offset is not None and not _looks_like_prose(text_lower[:offset]):
            score += STRONG_MARKER_PENALTY
    # Rounded so pieces whose weights sum exactly to the threshold
    # (e.g. 0.35 + 0.10 = 0.44999999999999996 in IEEE754) compare
    # deterministically against BOILERPLATE_THRESHOLD.
    return round(min(score, 1.0), 6)


def is_boilerplate(text: str, threshold: float = BOILERPLATE_THRESHOLD) -> bool:
    """True when ``text`` should be rejected as boilerplate."""
    return boilerplate_score(text) >= threshold


def body_is_usable(text: str) -> bool:
    """True when an extracted body is long enough and not mostly boilerplate.

    Repetition features accumulate over a full article, so scoring the whole
    body at once misfires: a how-to repeating "click Settings" per platform
    and a9k-token transcript both cross the chunk threshold despite every
    local region being clean prose. Instead, score fixed windows and reject
    only when nearly all of them are junk (a pure chrome/listing extraction).
    A few bad tail chunks are the chunk filter's job, not the gate's.
    """
    if not text or len(text.strip()) < MIN_BODY_CHARS:
        return False
    windows = [text[i : i + _GATE_WINDOW_CHARS] for i in range(0, len(text), _GATE_WINDOW_CHARS)]
    junk = sum(1 for w in windows if is_boilerplate(w))
    return junk / len(windows) < _GATE_JUNK_FRACTION


def count_rejected(pieces: list[str]) -> tuple[list[str], int]:
    """Split ``pieces`` into (kept, rejected_count) for the pre-embed filter."""
    kept: list[str] = []
    rejected = 0
    for piece in pieces:
        if is_boilerplate(piece):
            rejected += 1
        else:
            kept.append(piece)
    return kept, rejected
