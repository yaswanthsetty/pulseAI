"""Unit tests for FR-2 duplicate detection primitives."""

from datetime import UTC, datetime, timedelta

from backend.modules.ingestion.dedupe import (
    is_duplicate_title,
    is_within_window,
    normalize_url,
    title_similarity,
    url_hash,
)


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_strips_fragment(self):
        assert normalize_url("https://example.com/a#section-2") == "https://example.com/a"

    def test_strips_tracking_params(self):
        url = "https://example.com/a?utm_source=rss&utm_medium=feed&id=42"
        assert normalize_url(url) == "https://example.com/a?id=42"

    def test_strips_default_port(self):
        assert normalize_url("https://example.com:443/a") == "https://example.com/a"

    def test_keeps_nondefault_port(self):
        assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"

    def test_collapses_trailing_slash(self):
        assert normalize_url("https://example.com/a/") == "https://example.com/a"

    def test_empty_input(self):
        assert normalize_url("") == ""


class TestUrlHash:
    def test_deterministic(self):
        assert url_hash("https://example.com/a") == url_hash("https://example.com/a")

    def test_sha256_length(self):
        assert len(url_hash("https://example.com/a")) == 64


class TestFuzzyTitle:
    def test_identical_titles_are_duplicates(self):
        assert is_duplicate_title("AI Startup Raises $200M", "AI Startup Raises $200M")

    def test_near_identical_titles_are_duplicates(self):
        a = "AI Startup Raises $200 Million in Series C Round"
        b = "AI Startup Raises $200 Million in Series C Round "
        assert title_similarity(a, b) > 0.95
        assert is_duplicate_title(a, b)

    def test_different_titles_are_not_duplicates(self):
        assert not is_duplicate_title(
            "AI Startup Raises $200 Million", "Election Law Passes in Parliament"
        )

    def test_lowercased_and_whitespace_normalized(self):
        assert is_duplicate_title("Hello   World", "hello world")

    def test_window_logic(self):
        base = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        assert is_within_window(base, base + timedelta(hours=2), window_hours=6)
        assert not is_within_window(base, base + timedelta(hours=12), window_hours=6)
