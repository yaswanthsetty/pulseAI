"""Intent-aware temporal ranking (FR-14/FR-15).

Provides pure functions that score and re-rank search candidates using
the ``ranking_configs`` table weights.  The retrieval module calls these
after candidate retrieval (and optionally after cross-encoder reranking).

Scoring formula (per candidate):

    blended = w_sim * sim
            + w_fresh * freshness
            + w_cred * credibility
            + w_event * event_signal

where each component is in [0, 1].
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.modules.retrieval.schemas import SearchResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent detection (FR-14)
# ---------------------------------------------------------------------------

# Keyword patterns for recency intent — queries that signal "I want recent news".
_RECENCY_PATTERNS = re.compile(
    r"\b(breaking|latest|just now|today|yesterday|this (?:week|month|year)|"
    r"recent|current|newest|update|news|live|happening)\b",
    re.IGNORECASE,
)

# Keyword patterns for historical intent — queries that signal "I want older, evergreen content."
_HISTORICAL_PATTERNS = re.compile(
    r"\b(history|origin of|how did|when (?:was|did)|timeline of|evolution of|"
    r"first (?:ever|time)|began|started|founded|since \d{4}|before \d{4}|"
    r"classic|vintage|retrospective|look back|archive)\b",
    re.IGNORECASE,
)

# Explicit year references (e.g., "AI in 2023") — lean historical.
_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")


def detect_intent(query: str) -> str:
    """Classify a search query into an intent: recency, historical, or default.

    This is a fast keyword heuristic — no model call.  Returns one of the
    three intent names that match the ``ranking_configs`` table keys.
    """
    recency_hits = len(_RECENCY_PATTERNS.findall(query))
    historical_hits = len(_HISTORICAL_PATTERNS.findall(query))

    # Year references strengthen historical signal.
    if _YEAR_PATTERN.search(query):
        historical_hits += 1

    if recency_hits > historical_hits:
        return "recency"
    if historical_hits > recency_hits:
        return "historical"
    return "default"


# ---------------------------------------------------------------------------
# Freshness decay (FR-15)
# ---------------------------------------------------------------------------


def compute_freshness_score(
    published_at: datetime | None,
    *,
    now: datetime | None = None,
    half_life_days: float = 7.0,
) -> float:
    """Exponential decay score based on article age.

    Returns a value in [0, 1] where 1.0 = published right now, 0.5 = one
    half-life ago, ~0.0 = very old.  Articles with no ``published_at`` get
    the minimum score (0.05) so they are not entirely excluded.
    """
    if published_at is None:
        return 0.05
    now = now or datetime.now(UTC)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    age_days = max((now - published_at).total_seconds() / 86400, 0)
    return math.exp(-math.log(2) * age_days / half_life_days)


# ---------------------------------------------------------------------------
# Component scores
# ---------------------------------------------------------------------------


def compute_credibility_score(credibility: float | None) -> float:
    """Normalize source credibility to [0, 1].  None defaults to neutral 0.5."""
    if credibility is None:
        return 0.5
    return max(0.0, min(1.0, float(credibility)))


def compute_event_signal(event_id: str | None) -> float:
    """Binary signal: 1.0 if article belongs to an event, 0.0 otherwise."""
    return 1.0 if event_id else 0.0


# ---------------------------------------------------------------------------
# Blended scoring (the main entry point)
# ---------------------------------------------------------------------------


def blend_scores(
    candidates: list[tuple[SearchResult, dict]],
    *,
    w_sim: float = 0.55,
    w_fresh: float = 0.20,
    w_cred: float = 0.15,
    w_event: float = 0.10,
    now: datetime | None = None,
    half_life_days: float = 7.0,
) -> list[SearchResult]:
    """Re-rank candidates using a weighted blend of similarity, freshness,
    credibility, and event membership.

    Each weight corresponds to a column in ``ranking_configs``.  The
    function normalizes the weights to sum to 1.0 if they don't already.

    Returns the candidates sorted by blended score (descending) with
    ``similarity_score`` updated to the blended score so the API response
    reflects the temporal ranking.
    """
    total = w_sim + w_fresh + w_cred + w_event
    if total <= 0:
        return candidates

    w_sim /= total
    w_fresh /= total
    w_cred /= total
    w_event /= total

    scored: list[tuple[SearchResult, dict, float]] = []
    for result, payload in candidates:
        sim = result.similarity_score  # raw cosine or reranker score [0, 1]
        fresh = compute_freshness_score(result.published_at, now=now, half_life_days=half_life_days)
        cred = compute_credibility_score(payload.get("credibility_score"))
        evt = compute_event_signal(payload.get("event_id"))

        blended = w_sim * sim + w_fresh * fresh + w_cred * cred + w_event * evt
        scored.append((result, payload, blended))

    scored.sort(key=lambda t: t[2], reverse=True)

    results: list[tuple[SearchResult, dict]] = []
    for result, payload, score in scored:
        result.similarity_score = round(score, 4)
        results.append((result, payload))
    return results
