"""Unit tests for FR-6 language detection and FR-7 category classification."""

from backend.modules.ingestion.classifier import (
    classify_category,
    detect_language,
    is_known_language,
)


class TestClassifyCategory:
    def test_politics(self):
        assert classify_category("Parliament Approves New Voting Law") == "politics"

    def test_technology(self):
        assert classify_category("AI startup unveils new software platform") == "technology"

    def test_sports(self):
        assert classify_category("Championship final decided in extra time") == "sports"

    def test_health(self):
        assert classify_category("Hospital reports new outbreak among patients") == "health"

    def test_falls_back_to_other(self):
        assert classify_category("A very neutral statement about nothing in particular") == "other"

    def test_uses_body_text(self):
        title = "Company update"
        body = "The merger was approved and shares rose sharply on the market."
        assert classify_category(title, body) == "business"


class TestLanguageDetection:
    def test_detects_english(self):
        text = (
            "The committee announced a new policy today. Several members voted "
            "in favor of the proposal after a long debate about the budget."
        )
        assert detect_language(text) == "en"

    def test_returns_none_for_short_text(self):
        assert detect_language("Hi") is None


class TestKnownLanguages:
    def test_known_code(self):
        assert is_known_language("en")
        assert is_known_language("fr")

    def test_unknown_code(self):
        assert not is_known_language("xx")
        assert not is_known_language(None)
