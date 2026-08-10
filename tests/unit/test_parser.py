"""Unit tests for feed parsing / HTML extraction (FR-4, FR-5)."""

from pathlib import Path

from backend.modules.ingestion.parser import (
    clean_html,
    extract_main_content,
    parse_feed,
    validate_feed,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestCleanHtml:
    def test_strips_tags_and_joins_words(self):
        assert clean_html("<p>Hello <b>world</b>!</p>") == "Hello world!"

    def test_empty(self):
        assert clean_html("") == ""


class TestExtractMainContent:
    def test_extracts_article_paragraphs(self):
        html = _fixture("article.html")
        text = extract_main_content(html)
        assert "AI Startup Raises $200 Million in Series C Round" in text
        assert "new capital will be used" in text
        # boilerplate removed
        assert "Home" not in text
        assert "tracking pixel" not in text
        assert "Copyright" not in text

    def test_empty(self):
        assert extract_main_content("") == ""


class TestParseFeed:
    def test_parses_entries(self):
        entries = parse_feed(_fixture("feed.xml"))
        assert len(entries) == 3

    def test_metadata_extracted(self):
        entry = parse_feed(_fixture("feed.xml"))[0]
        assert entry.title == "AI Startup Raises $200 Million in Series C Round"
        assert entry.url.startswith("https://fixture.example.com/articles/ai-startup-series-c")
        assert entry.author == "Jane Reporter"
        assert entry.image_url == "https://fixture.example.com/img/ai.jpg"
        assert entry.language_hint == "en"
        assert (
            entry.summary
            == "The AI company announced a $200 million Series C led by top investors."
        )

    def test_skips_entries_without_link(self):
        broken = _fixture("feed.xml").replace(
            "<link>https://fixture.example.com/articles/ai-startup-series-c?utm_source=rss&amp;utm_medium=feed</link>",
            "",
        )
        assert len(parse_feed(broken)) == 2


class TestValidateFeed:
    def test_accepts_well_formed_feed(self):
        result = validate_feed(_fixture("feed.xml"))
        assert result.valid is True
        assert result.title == "Fixture Tech News"
        assert result.entry_count == 3

    def test_rejects_garbage(self):
        result = validate_feed("this is not xml at all {{{")
        assert result.valid is False
        assert result.error
