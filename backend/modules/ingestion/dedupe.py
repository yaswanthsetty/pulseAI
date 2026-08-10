"""Duplicate detection (FR-2).

Two complementary mechanisms:

1. **Exact/normalized URL**: every URL is normalized (scheme/host lowercased,
   default ports, fragments and tracking parameters stripped, trailing slash
   collapsed) and hashed with SHA-256 into ``articles.url_hash`` — a fast,
   indexed uniqueness check.
2. **Fuzzy title match**: syndicated copies often reuse the same story with a
   slightly different URL. Articles from the *same source* published within a
   small time window whose normalized titles are near-identical are treated
   as duplicates (title + source + published-date fuzzy match per FR-2).
"""

import hashlib
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import Article

# Query parameters that carry no content identity (UTM tracking, share click ids).
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "yclid",
        "spm",
    }
)

_WHITESPACE = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Return a canonical form of *url* for identity comparison."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return url

    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    """SHA-256 hex digest of a normalized URL (64 chars, FR-2)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    """Lowercase, whitespace-collapsed title used for fuzzy comparison."""
    return _WHITESPACE.sub(" ", (title or "").strip().lower())


def title_similarity(a: str, b: str) -> float:
    """Ratcliff/Obershelp similarity ratio between two titles (0..1)."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def is_duplicate_title(a: str, b: str, threshold: float | None = None) -> bool:
    threshold = settings.fuzzy_duplicate_threshold if threshold is None else threshold
    return title_similarity(a, b) >= threshold


def is_within_window(a: datetime, b: datetime, window_hours: int | None = None) -> bool:
    window_hours = settings.fuzzy_duplicate_window_hours if window_hours is None else window_hours
    return abs((a - b).total_seconds()) <= window_hours * 3600


def find_fuzzy_duplicate(
    db: Session,
    source_id,
    title: str,
    published_at: datetime,
    *,
    exclude_article_id=None,
) -> Article | None:
    """Return an existing article that is a near-duplicate of *title*.

    Only same-source articles published within the configured window are
    considered, matching FR-2's title + source + published-date key.
    """
    if not title:
        return None
    window = timedelta(hours=settings.fuzzy_duplicate_window_hours)

    statement = select(Article).where(
        Article.source_id == source_id,
        Article.published_at >= published_at - window,
        Article.published_at <= published_at + window,
    )
    if exclude_article_id is not None:
        statement = statement.where(Article.id != exclude_article_id)

    candidates = db.execute(statement).scalars().all()
    for candidate in candidates:
        if is_duplicate_title(title, candidate.title):
            return candidate
    return None
