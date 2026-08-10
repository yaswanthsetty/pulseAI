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
from bs4 import BeautifulSoup

from backend.core.config import settings

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


def extract_main_content(html: str, max_chars: int | None = None) -> str:
    """Best-effort extraction of the main article text from a full HTML page.

    Heuristic (no trained model): drop boilerplate elements, prefer
    <article>/<main>/role=main, then concatenate paragraph/heading text.
    Falls back to the whole body when no paragraph elements exist.
    """
    if not html:
        return ""
    max_chars = max_chars or settings.max_article_storage_chars

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]
    ):
        tag.decompose()

    article = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
    root = article if article is not None else (soup.body or soup)

    blocks = root.find_all(["p", "h1", "h2", "h3", "blockquote", "li"])
    if blocks:
        text = "\n".join(
            block.get_text(" ", strip=True) for block in blocks if block.get_text(strip=True)
        )
    else:
        text = root.get_text(" ", strip=True)

    text = " ".join(text.split())
    return text[:max_chars]


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
