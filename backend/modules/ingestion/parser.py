"""RSS/Atom parsing, HTML cleaning, and main-content extraction (FR-5).

Also owns feed *well-formedness* validation used by FR-4 (admin source
addition) and by the poll path.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import trafilatura
from bs4 import BeautifulSoup
from trafilatura.deduplication import LRUCache

from backend.core.config import settings
from backend.core.content_quality import MIN_BODY_CHARS

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"\s+([,.;:!?])")


@dataclass
class FeedEntry:
    """A single normalized entry extracted from a feed."""

    title: str
    url: str
    summary: str
    author: str | None
    published_at: datetime
    image_url: str | None
    language_hint: str | None


@dataclass
class FeedValidation:
    """Result of validating a candidate RSS/Atom feed (FR-4)."""

    valid: bool
    title: str = ""
    feed_type: str = ""
    entry_count: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

#: Class/id fragments that mark non-article chrome divs (TechCrunch-style
#: rails, promos, newsletter blocks). Matched case-insensitively as substrings.
_CHROME_RE = re.compile(
    r"nav|sidebar|side-bar|related|popular|newsletter|promo|widget|share|social|"
    r"breadcrumb|advert|masthead|banner|recommend|in-brief|latest-in",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    """Extracted article text plus which extractor produced it."""

    text: str
    extractor: str  # "trafilatura" | "fallback"


def clean_html(raw_html: str) -> str:
    """Strip tags from feed summaries/descriptions, collapsing whitespace."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    # Avoid space-before-punctuation artifacts from tag boundaries.
    return _PUNCT_RE.sub(r"\1", text)


def _chrome_elements(root) -> list:
    """Elements inside ``root`` whose class/id looks like nav chrome."""
    found = []
    for tag in root.find_all(True):
        if tag.parent is None:  # already decomposed via an ancestor
            continue
        marker = " ".join(tag.get("class") or []) + " " + str(tag.get("id") or "")
        if marker and _CHROME_RE.search(marker):
            found.append(tag)
    return found


def _text_blocks(root, *, allow_list_items: bool) -> str:
    tags = ["p", "h1", "h2", "h3", "blockquote"]
    if allow_list_items:
        tags.append("li")
    blocks = root.find_all(tags)
    if blocks:
        text = "\n".join(
            block.get_text(" ", strip=True) for block in blocks if block.get_text(strip=True)
        )
    else:
        text = root.get_text(" ", strip=True)
    return " ".join(text.split())


def extract_main_content(html: str, max_chars: int | None = None) -> str:
    """Heuristic BeautifulSoup extraction (fallback path).

    Hardened against TechCrunch-style pages:

    * picks the ``<article>`` with the *most paragraph text* instead of the
      first one (the first is often a card/teaser);
    * strips div-level chrome (rails, promos, newsletter blocks) by class/id;
    * ``<li>`` items are only collected when they sit inside the chosen
      ``<article>`` body - a nav list in ``<body>`` is never article text.
    """
    if not html:
        return ""
    max_chars = max_chars or settings.max_article_storage_chars

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]
    ):
        tag.decompose()

    # Choose the article element with the most paragraph text (not the first).
    articles = soup.find_all("article")
    root = None
    from_article = False
    if articles:
        root = max(articles, key=lambda a: len(a.get_text(" ", strip=True)))
        from_article = True
    else:
        root = soup.find("main") or soup.find(attrs={"role": "main"})
    if root is None:
        root = soup.body or soup

    for tag in _chrome_elements(root):
        tag.decompose()

    text = _text_blocks(root, allow_list_items=from_article)
    return text[:max_chars]


def extract_article(html: str, max_chars: int | None = None) -> ExtractionResult:
    """Primary extraction: trafilatura (precision) with BS fallback.

    ``ExtractionResult.extractor`` records which path won so ingestion can
    store it per article (``articles.extractor``) and log the mix.
    """
    if not html:
        return ExtractionResult(text="", extractor="fallback")
    try:
        text = trafilatura.extract(
            html,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
            # deduplicate=True uses a process-global document cache: the
            # second identical document (syndicated HTML, re-extraction,
            # re-ingest) silently returns None and downgrades to fallback.
            # A per-call scoped cache keeps within-document segment dedup
            # while making extraction deterministic.
            deduplicate=LRUCache(),
        )
    except Exception as exc:  # noqa: BLE001 - third-party parser must not kill ingestion
        logger.warning("trafilatura failed, using BS fallback: %s", exc)
        text = None
    if text and len(text) >= MIN_BODY_CHARS:
        max_chars = max_chars or settings.max_article_storage_chars
        return ExtractionResult(text=text[:max_chars], extractor="trafilatura")
    return ExtractionResult(text=extract_main_content(html, max_chars), extractor="fallback")


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def _entry_image(entry) -> str | None:
    """Pull the best available image URL from a feedparser entry."""
    for attr in ("media_content", "media_thumbnail"):
        media = getattr(entry, attr, None)
        if media:
            for item in media:
                url = item.get("url") or item.get("href")
                if url:
                    return url
    for attr in ("image", "media_image"):
        img = getattr(entry, attr, None)
        if img:
            url = getattr(img, "url", None) if not isinstance(img, str) else img
            if url:
                return url
    summary = getattr(entry, "summary", "") or ""
    if "<img" in summary.lower():
        soup = BeautifulSoup(summary, "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]
    return None


def _parse_date(entry) -> datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        try:
            return datetime.fromtimestamp(time.mktime(parsed), tz=UTC)
        except ValueError, OverflowError:
            pass
    return datetime.now(UTC)


def _language_code(value: str | None) -> str | None:
    """Normalize a language string to an ISO 639-1 code (e.g. 'en-US' -> 'en')."""
    if not value:
        return None
    code = value.strip().lower().split("-")[0]
    return code or None


def parse_feed(content: str) -> list[FeedEntry]:
    """Parse RSS/Atom content into normalized entries (skips unusable rows)."""
    feed = feedparser.parse(content)
    feed_language = _language_code(feed.feed.get("language"))
    entries: list[FeedEntry] = []

    for entry in feed.entries:
        title = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        if not title or not link:
            continue

        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        summary = clean_html(raw_summary)
        language = _language_code(getattr(entry, "language", None)) or feed_language

        entries.append(
            FeedEntry(
                title=str(title).strip(),
                url=str(link).strip(),
                summary=summary,
                author=getattr(entry, "author", None) or None,
                published_at=_parse_date(entry),
                image_url=_entry_image(entry),
                language_hint=language,
            )
        )
    return entries


def validate_feed(content: str) -> FeedValidation:
    """Return whether *content* is a well-formed RSS/Atom feed (FR-4)."""
    feed = feedparser.parse(content)
    if feed.bozo:
        return FeedValidation(valid=False, error=str(feed.bozo_exception or "malformed feed"))
    if not feed.entries:
        return FeedValidation(valid=False, error="feed contains no entries")
    return FeedValidation(
        valid=True,
        title=feed.feed.get("title", ""),
        feed_type=feed.version or "rss",
        entry_count=len(feed.entries),
    )
