"""Outbound HTTP fetching with SSRF protection (spec §23).

Every fetch — feed polling and article-page retrieval — goes through
:func:`fetch_url`, which first validates the target via the SSRF guard and
then enforces a timeout and a polite User-Agent.
"""

import logging

import httpx

from backend.core.config import settings
from backend.core.ssrf import UnsafeUrlError, check_url

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when an HTTP fetch fails (timeout, status, transport)."""


def fetch_url(url: str, timeout: float | None = None) -> str:
    """Fetch *url* and return its body as text. Raises FetchError/UnsafeUrlError."""
    safety = check_url(url)
    if not safety.safe:
        raise UnsafeUrlError(f"blocked fetch to {url}: {safety.reason}")

    effective_timeout = timeout if timeout is not None else settings.feed_fetch_timeout_seconds
    try:
        with httpx.Client(
            timeout=effective_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.http_user_agent},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.HTTPError as exc:
        raise FetchError(f"failed to fetch {url}: {exc}") from exc
