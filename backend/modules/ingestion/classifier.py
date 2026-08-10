"""Language detection and category classification (FR-6 / FR-7).

Language detection uses ``langdetect`` (a lightweight, offline detector).
Category classification uses a deterministic keyword/taxonomy scorer against
the fixed, versioned category set — fast, offline, and testable. Both are
deliberately isolated behind small functions so Phase 2+ can swap in
fasttext / a zero-shot classifier without touching the pipeline.
"""

import logging
import re

from langdetect import DetectorFactory, LangDetectException, detect

from backend.core.config import settings
from backend.db.seed_data import LANGUAGES

logger = logging.getLogger(__name__)

# Deterministic results across runs (langdetect's random seed).
DetectorFactory.seed = 0

# Known ISO 639-1 codes present in the ``languages`` lookup table (FK-safe).
KNOWN_LANGUAGE_CODES = {row["code"] for row in LANGUAGES}

# Fixed taxonomy (FR-7) — order defines tie-breaking priority.
CATEGORY_ORDER = [
    "politics",
    "business",
    "technology",
    "science",
    "health",
    "sports",
    "entertainment",
    "world",
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "politics": [
        "election",
        "president",
        "senate",
        "congress",
        "parliament",
        "vote",
        "government",
        "minister",
        "policy",
        "legislation",
        "campaign",
        "diplomat",
        "embassy",
        "lawmaker",
        "referendum",
        "bipartisan",
        "candidate",
    ],
    "business": [
        "market",
        "stock",
        "shares",
        "economy",
        "inflation",
        "recession",
        "earnings",
        "revenue",
        "merger",
        "acquisition",
        "bank",
        "investor",
        "trade",
        "tariff",
        "gdp",
        "ceo",
        "funding",
        "ipo",
        "dividend",
        "federal reserve",
    ],
    "technology": [
        "artificial intelligence",
        "software",
        "app",
        "chip",
        "semiconductor",
        "cyber",
        "hack",
        "data breach",
        "cloud",
        "robot",
        "algorithm",
        "smartphone",
        "gadget",
        "internet",
        "blockchain",
        "quantum",
        "startup",
        "platform",
    ],
    "science": [
        "research",
        "study",
        "scientist",
        "nasa",
        "space",
        "planet",
        "gene",
        "climate",
        "physics",
        "biology",
        "experiment",
        "discovery",
        "species",
        "particle",
        "astronomer",
        "fossil",
    ],
    "health": [
        "health",
        "hospital",
        "doctor",
        "patient",
        "disease",
        "outbreak",
        "virus",
        "pandemic",
        "covid",
        "cancer",
        "drug",
        "treatment",
        "medical",
        "mental health",
        "obesity",
        "fda",
        "vaccine",
    ],
    "sports": [
        "football",
        "soccer",
        "basketball",
        "cricket",
        "tennis",
        "olympics",
        "championship",
        "match",
        "league",
        "coach",
        "player",
        "goal",
        "tournament",
        "nba",
        "nfl",
        "f1",
        "formula one",
    ],
    "entertainment": [
        "movie",
        "film",
        "actor",
        "actress",
        "album",
        "song",
        "concert",
        "netflix",
        "hollywood",
        "oscar",
        "celebrity",
        "theater",
        "gaming",
        "award",
        "series",
        "music",
    ],
    "world": [
        "international",
        "global",
        "united nations",
        "war",
        "conflict",
        "foreign",
        "border",
        "refugee",
        "sanction",
        "summit",
        "g20",
        "diplomatic",
    ],
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def is_known_language(code: str | None) -> bool:
    """True when *code* exists in the ``languages`` lookup table (FK-safe)."""
    return code is not None and code in KNOWN_LANGUAGE_CODES


def detect_language(text: str) -> str | None:
    """Detect the ISO 639-1 code of *text*; None when uncertain or too short."""
    sample = " ".join((text or "").split())[:1000]
    if len(sample) < 20:
        return None
    try:
        return detect(sample).split("-")[0]
    except LangDetectException:
        return None


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def classify_category(title: str, body: str = "") -> str:
    """Classify text into the fixed taxonomy; returns a category ``code``.

    Scoring: single-word keywords match on token boundaries; multi-word
    keywords match as substrings. Highest score wins, ties broken by
    ``CATEGORY_ORDER`` priority; no hits → ``other``.
    """
    text = f"{title} {body}".lower()
    tokens = _tokens(text)

    best = "other"
    best_score = 0
    for category in CATEGORY_ORDER:
        score = 0
        for keyword in CATEGORY_KEYWORDS[category]:
            if " " in keyword:
                if keyword in text:
                    score += 2
            elif keyword in tokens:
                score += 1
        if score > best_score:
            best = category
            best_score = score
    return best


def classify_article(title: str, body: str) -> str:
    """Category classification entry point used by the processing pipeline."""
    if settings.debug:  # pragma: no cover - debugging aid
        logger.debug("classify(title=%r)", title)
    return classify_category(title, body)
