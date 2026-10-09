"""Unit tests for feed parsing / HTML extraction (FR-4, FR-5)."""

from pathlib import Path

from backend.modules.ingestion.parser import (
    clean_html,
    extract_article,
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


class TestExtractArticle:
    """trafilatura primary -> hardened BS fallback (Phase 1.2 clean extraction)."""

    def test_trafilatura_wins_on_techcrunch_shape_with_sidebar(self):
        result = extract_article(_fixture("html/tc_sidebar.html"))
        assert result.extractor == "trafilatura"
        assert "speculative decoding" in result.text  # body survives
        assert "Most Popular" not in result.text  # related rail gone
        assert "Newsletters Subscribe" not in result.text
        assert "Trending" not in result.text
        assert "All rights reserved" not in result.text

    def test_extraction_is_deterministic_across_repeated_calls(self):
        """trafilatura's global doc-dedup must not downgrade repeats to fallback."""
        html = _fixture("html/tc_sidebar.html")
        results = [extract_article(html) for _ in range(5)]
        assert all(r.extractor == "trafilatura" for r in results)
        assert len({r.text for r in results}) == 1

    def test_plain_blog_body_survives(self):
        result = extract_article(_fixture("html/plain_blog.html"))
        assert result.extractor == "trafilatura"
        assert "two-phase approach" in result.text
        assert "rollback" in result.text

    def test_page_without_article_tag(self):
        result = extract_article(_fixture("html/no_article.html"))
        assert result.extractor == "trafilatura"
        assert "wheelchair users near the transit station" in result.text
        assert "vans" in result.text

    def test_empty(self):
        assert extract_article("").text == ""


class TestNavTailRegression:
    """d9569671-style case: nav <li> tail inside <article> must not survive."""

    def test_fallback_drops_related_posts_list(self):
        text = extract_main_content(_fixture("html/tc_nav_tail.html"))
        assert "structural problem the industry has failed" in text  # body kept
        assert "Most Popular" not in text
        assert "OpenRouter" not in text
        assert "spyware attack" not in text

    def test_primary_path_drops_it_too(self):
        result = extract_article(_fixture("html/tc_nav_tail.html"))
        assert "structural problem the industry has failed" in result.text
        assert "Most Popular" not in result.text
        assert "OpenRouter" not in result.text

    def test_fallback_picks_article_with_most_text_not_first(self):
        html = """
        <html><body>
          <article class="teaser"><p>Card teaser blurb only.</p></article>
          <article>
            <h1>Real headline here</h1>
            <p>First real paragraph with a decent amount of words to win the pick on length.</p>
            <p>Second real paragraph also with a decent amount of words to be selected as root.</p>
            <p>Third real paragraph with enough words so this article clearly has more text.</p>
          </article>
        </body></html>
        """
        text = extract_main_content(html)
        assert "Third real paragraph" in text
        assert "Card teaser" not in text


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
