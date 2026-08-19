"""Tests for intent-aware temporal ranking (FR-14/FR-15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.modules.ranking.service import (
    blend_scores,
    compute_credibility_score,
    compute_event_signal,
    compute_freshness_score,
    detect_intent,
)
from backend.modules.retrieval.schemas import SearchResult

# ---------------------------------------------------------------------------
# Intent detection (FR-14)
# ---------------------------------------------------------------------------


class TestDetectIntent:
    def test_recency_keywords(self):
        assert detect_intent("latest AI news") == "recency"
        assert detect_intent("breaking: OpenAI launches") == "recency"
        assert detect_intent("what happened today in tech") == "recency"
        assert detect_intent("just now: Trump signs") == "recency"

    def test_historical_keywords(self):
        assert detect_intent("history of the internet") == "historical"
        assert detect_intent("when was AI first invented") == "historical"
        assert detect_intent("evolution of smartphones") == "historical"
        assert detect_intent("AI in 2023") == "historical"

    def test_default_when_ambiguous(self):
        assert detect_intent("artificial intelligence") == "default"
        assert detect_intent("climate change solutions") == "default"
        assert detect_intent("startup funding rounds") == "default"

    def test_empty_string(self):
        assert detect_intent("") == "default"

    def test_recency_wins_over_historical(self):
        # "latest" + no historical keyword → recency
        assert detect_intent("latest developments in computing") == "recency"

    def test_historical_wins_over_default(self):
        assert detect_intent("evolution of AI") == "historical"


# ---------------------------------------------------------------------------
# Freshness decay (FR-15)
# ---------------------------------------------------------------------------


class TestFreshnessScore:
    def test_just_published(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        score = compute_freshness_score(now, now=now)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_one_half_life(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        published = now - timedelta(days=7)
        score = compute_freshness_score(published, now=now)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_two_half_lives(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        published = now - timedelta(days=14)
        score = compute_freshness_score(published, now=now)
        assert score == pytest.approx(0.25, abs=0.02)

    def test_very_old(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        published = now - timedelta(days=90)
        score = compute_freshness_score(published, now=now)
        assert score < 0.01

    def test_none_published_gets_minimum(self):
        score = compute_freshness_score(None)
        assert score == 0.05

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 8, 19)
        now = datetime(2026, 8, 19, tzinfo=UTC)
        score = compute_freshness_score(naive, now=now)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_custom_half_life(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        published = now - timedelta(days=1)
        score = compute_freshness_score(published, now=now, half_life_days=1.0)
        assert score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Component scores
# ---------------------------------------------------------------------------


class TestComponentScores:
    def test_credibility_none(self):
        assert compute_credibility_score(None) == 0.5

    def test_credibility_clamped(self):
        assert compute_credibility_score(1.0) == 1.0
        assert compute_credibility_score(0.0) == 0.0
        assert compute_credibility_score(0.85) == 0.85

    def test_event_signal(self):
        assert compute_event_signal("some-uuid") == 1.0
        assert compute_event_signal(None) == 0.0
        assert compute_event_signal("") == 0.0


# ---------------------------------------------------------------------------
# Blended scoring
# ---------------------------------------------------------------------------


def _make_result(
    sim: float = 0.8,
    published_days_ago: int = 1,
    cred: float = 0.9,
    event_id: str | None = None,
) -> tuple[SearchResult, dict]:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return (
        SearchResult(
            article_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            source_id="00000000-0000-0000-0000-000000000002",
            similarity_score=sim,
            published_at=now - timedelta(days=published_days_ago),
        ),
        {"credibility_score": cred, "event_id": event_id},
    )


class TestBlendScores:
    def test_fresh_article_beats_old_high_sim(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        fresh = _make_result(sim=0.6, published_days_ago=1, cred=0.5, event_id=None)
        old = _make_result(sim=0.9, published_days_ago=30, cred=0.5, event_id=None)
        results = blend_scores(
            [fresh, old], w_sim=0.35, w_fresh=0.40, w_cred=0.15, w_event=0.10, now=now
        )
        assert results[0][0].article_id == fresh[0].article_id

    def test_high_cred_wins_tie(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        a = _make_result(sim=0.8, published_days_ago=1, cred=0.3, event_id=None)
        b = _make_result(sim=0.8, published_days_ago=1, cred=0.9, event_id=None)
        results = blend_scores([a, b], w_sim=0.55, w_fresh=0.20, w_cred=0.15, w_event=0.10, now=now)
        assert results[0][0].article_id == b[0].article_id

    def test_event_article_gets_boost(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        no_event = _make_result(sim=0.7, published_days_ago=5, cred=0.5, event_id=None)
        with_event = _make_result(sim=0.7, published_days_ago=5, cred=0.5, event_id="some-uuid")
        results = blend_scores(
            [no_event, with_event],
            w_sim=0.55,
            w_fresh=0.20,
            w_cred=0.15,
            w_event=0.10,
            now=now,
        )
        assert results[0][0].article_id == with_event[0].article_id

    def test_scores_are_normalized(self):
        now = datetime(2026, 8, 19, tzinfo=UTC)
        items = [_make_result(sim=0.5, published_days_ago=3) for _ in range(3)]
        results = blend_scores(items, w_sim=2, w_fresh=4, w_cred=3, w_event=1, now=now)
        for r_result, _payload in results:
            assert 0.0 <= r_result.similarity_score <= 1.0

    def test_empty_candidates(self):
        results = blend_scores([])
        assert results == []

    def test_zero_weights_returns_unsorted(self):
        items = [_make_result(sim=0.8)]
        results = blend_scores(items, w_sim=0, w_fresh=0, w_cred=0, w_event=0)
        assert len(results) == 1
