"""Grounding helpers for chat and reports (Upgrade Plan, Phase 1).

Pure functions — no I/O, no DB — so they are trivially unit-testable.

* ``build_context`` turns retrieval hits into a token-budgeted block of
  ``[#n] Source | date | Title`` headers followed by the real chunk text, plus
  the matching ``EvidenceItem`` list.
* ``compute_context_budget`` derives how many tokens of excerpts fit, from the
  Ollama ``num_ctx``/``num_predict`` settings and the fixed prompt parts.
* ``sanitize_citations`` strips ``[#n]`` markers the model invented.

Architecture note
-----------------
Like ``agents/service.py`` this module MUST NOT import sibling business modules.
``SearchResult`` is referenced under ``TYPE_CHECKING`` only (attribute access).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.modules.agents.schemas import EvidenceItem

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from backend.modules.retrieval.schemas import SearchResult

# Share of ``num_ctx`` held back as a safety margin for tokenizer drift and
# chat-template tokens our estimate does not see.
SAFETY_MARGIN = 0.10
# Chat-template tokens for the system + user messages (role markers, etc.).
MESSAGE_OVERHEAD_TOKENS = 32
# Separator ("\n\n") between context blocks, in tokens.
_BLOCK_SEPARATOR_TOKENS = 2
# An oversized *first* article is truncated rather than dropped, but only when at
# least this many tokens remain for its text.
_MIN_TRUNCATED_CHUNK_TOKENS = 32
SNIPPET_CHARS = 240
NO_CONTEXT_PLACEHOLDER = "(no articles retrieved)"

_CONTEXT_TAG_RE = re.compile(r"</?\s*context\s*>", re.IGNORECASE)
_CITATION_RE = re.compile(r"([ \t]*)\[#(\d+(?:\s*,\s*#?\d+)*)\]")
_NUMBER_RE = re.compile(r"\d+")


# ---------------------------------------------------------------------------
# Token estimation + budget
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for a Qwen-style BPE tokenizer.

    ASCII text averages ~4 chars/token, so 3.5 over-counts slightly. Non-ASCII
    scripts (Hindi, Telugu, CJK, …) can cost about one token per character, so
    they are counted 1:1 — the budget must hold for multilingual articles.
    """
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_chars = len(text) - non_ascii
    return math.ceil(ascii_chars / 3.5 + non_ascii)


def prompt_token_limit(num_ctx: int, num_predict: int) -> int:
    """Max prompt tokens: ``num_ctx`` minus the safety margin minus ``num_predict``."""
    return max(0, int(num_ctx * (1 - SAFETY_MARGIN)) - num_predict)


def compute_context_budget(
    num_ctx: int,
    num_predict: int,
    fixed_texts: Iterable[str],
    cap: int = 0,
) -> int:
    """Tokens available for article excerpts.

    ``num_ctx`` − 10% safety margin − ``num_predict`` − tokens of ``fixed_texts``
    (system prompt, question, wrapper text) − chat-template overhead. ``cap`` > 0
    additionally bounds the result. Never negative.
    """
    fixed = sum(estimate_tokens(t) for t in fixed_texts) + MESSAGE_OVERHEAD_TOKENS
    budget = max(0, prompt_token_limit(num_ctx, num_predict) - fixed)
    if cap > 0:
        budget = min(budget, cap)
    return budget


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Cut ``text`` to at most ``max_tokens`` (estimated), on a word boundary."""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    # Reserve one token for the ellipsis; binary-search the longest fitting prefix.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens - 1:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary > lo * 0.6:
        cut = cut[:boundary]
    cut = cut.rstrip()
    return cut + "…" if cut else ""


def fit_question(question: str, num_ctx: int, num_predict: int) -> str:
    """Bound a (user-supplied) question to a third of the prompt allowance.

    Keeps room for the system prompt and excerpts even if the user pastes an
    enormous message; the stored message is never altered.
    """
    return truncate_to_tokens(question, max(64, prompt_token_limit(num_ctx, num_predict) // 3))


def fit_texts(texts: Sequence[str], max_tokens: int) -> list[str]:
    """Shrink ``texts`` so their total is within ``max_tokens``.

    Short items are kept whole; the remaining budget is shared evenly among the
    longer ones (water-filling), so no sub-answer is dropped entirely.
    """
    sizes = [estimate_tokens(t) for t in texts]
    if sum(sizes) <= max_tokens:
        return list(texts)
    limits = [0] * len(texts)
    remaining = max(0, max_tokens)
    order = sorted(range(len(texts)), key=lambda i: sizes[i])
    for pos, i in enumerate(order):
        share = remaining // (len(texts) - pos)
        limits[i] = min(sizes[i], share)
        remaining -= limits[i]
    return [truncate_to_tokens(t, limits[i]) for i, t in enumerate(texts)]


def make_snippet(text: str, max_chars: int = SNIPPET_CHARS) -> str:
    """First ~``max_chars`` of ``text`` (whitespace collapsed), cut on a word boundary."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars]
    boundary = cut.rfind(" ")
    if boundary > max_chars * 0.5:
        cut = cut[:boundary]
    return cut.rstrip(" ,;:.-") + "…"


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


class CitationRegistry:
    """Stable ``[#n]`` ids per article, shared across the deep path's sub-questions."""

    def __init__(self) -> None:
        self._items: dict[object, EvidenceItem] = {}

    def __len__(self) -> int:
        return len(self._items)

    def next_id(self) -> int:
        return len(self._items) + 1

    def get(self, article_id: object) -> EvidenceItem | None:
        return self._items.get(article_id)

    def add(self, item: EvidenceItem) -> None:
        self._items[item.article_id] = item

    def items(self) -> list[EvidenceItem]:
        return list(self._items.values())


@dataclass
class GroundedContext:
    """Result of ``build_context``."""

    text: str  # the excerpts block (without <context> wrapper)
    evidence: list[EvidenceItem] = field(default_factory=list)
    dropped_chunks: int = 0

    @property
    def valid_ids(self) -> set[int]:
        return {e.citation_id for e in self.evidence}


@dataclass
class _Chunk:
    text: str
    chunk_id: object | None


@dataclass
class _Selected:
    result: SearchResult
    chunks: list[_Chunk]  # chunks[0] is the primary; may be empty (header-only)
    candidates: list[_Chunk]  # every chunk we could have used (for drop counting)


def _clean(text: str | None) -> str:
    """Remove ``<context>`` tags so retrieved text cannot close our wrapper."""
    return _CONTEXT_TAG_RE.sub("", text or "").strip()


def _strip_title(text: str, title: str) -> str:
    """Chunk 0 is stored as ``"{title}\\n\\n{body}"``; the header already has the title."""
    if title and text.startswith(title):
        return text[len(title) :].lstrip()
    return text


def _candidate_chunks(result: SearchResult, max_chunks: int) -> list[_Chunk]:
    title = _clean(result.title)
    chunks: list[_Chunk] = []
    primary = _strip_title(_clean(result.chunk_text), title)
    if primary:
        chunks.append(_Chunk(primary, result.chunk_id))
    for extra in (result.extra_chunks or [])[: max(0, max_chunks - 1)]:
        text = _strip_title(_clean(extra.chunk_text), title)
        if text and (not chunks or text != chunks[0].text):
            chunks.append(_Chunk(text, extra.chunk_id))
    return chunks[: max(1, max_chunks)]


def _header(citation_id: int, result: SearchResult) -> str:
    source = _clean(result.source_name) or "Unknown source"
    date = result.published_at.strftime("%Y-%m-%d") if result.published_at else "date unknown"
    return f"[#{citation_id}] {source} | {date} | {_clean(result.title)}"


def build_context(
    results: Sequence[SearchResult],
    *,
    token_budget: int,
    max_chunks_per_article: int = 2,
    registry: CitationRegistry | None = None,
) -> GroundedContext:
    """Build the excerpts block + evidence list within ``token_budget`` tokens.

    Articles are taken in rank order. Pass 1 gives each article its best chunk;
    pass 2 adds second chunks while budget remains. Filling stops at the first
    thing that does not fit (rank order is preserved; nothing is skipped over).
    The first article is truncated rather than dropped when it alone is too
    large. Articles that do not fit are *not* in ``evidence`` — ids are only
    assigned to what the model actually sees. Results without chunk text appear
    as header-only blocks.
    """
    registry = registry if registry is not None else CitationRegistry()
    remaining = max(0, token_budget)
    selected: list[_Selected] = []
    dropped = 0
    seen_articles: set[object] = set()
    pending_new = 0  # new ids handed out in this call so far

    # ── Pass 1: one primary chunk per article, in rank order ──────────────
    stopped = False
    for result in results:
        candidates = _candidate_chunks(result, max_chunks_per_article)
        weight = max(1, len(candidates))
        if stopped:
            dropped += weight
            continue
        if result.article_id in seen_articles:
            continue  # defensive: one block per article
        existing = registry.get(result.article_id)
        tentative_id = existing.citation_id if existing else registry.next_id() + pending_new
        header_cost = estimate_tokens(_header(tentative_id, result)) + _BLOCK_SEPARATOR_TOKENS
        primary = candidates[0] if candidates else None
        cost = header_cost + (estimate_tokens(primary.text) if primary else 0)

        chosen: list[_Chunk] | None = None
        if cost <= remaining:
            chosen = [primary] if primary else []
            remaining -= cost
        elif not selected and primary and remaining - header_cost >= _MIN_TRUNCATED_CHUNK_TOKENS:
            clipped = truncate_to_tokens(primary.text, remaining - header_cost)
            chosen = [_Chunk(clipped, primary.chunk_id)]
            remaining = 0
        if chosen is None:
            stopped = True
            dropped += weight
            continue

        seen_articles.add(result.article_id)
        if existing is None:
            pending_new += 1
        selected.append(_Selected(result=result, chunks=chosen, candidates=candidates))

    # ── Pass 2: second chunks, in rank order, while budget remains ────────
    for sel in selected:
        for extra in sel.candidates[1:]:
            cost = estimate_tokens(extra.text) + 1
            if cost <= remaining:
                sel.chunks.append(extra)
                remaining -= cost
            else:
                dropped += 1
                remaining = 0  # stop adding anywhere once one is refused

    # ── Assign ids (only to what made it in) and render ───────────────────
    evidence: list[EvidenceItem] = []
    blocks: list[str] = []
    for sel in selected:
        result = sel.result
        item = registry.get(result.article_id)
        if item is None:
            primary = sel.chunks[0] if sel.chunks else None
            item = EvidenceItem(
                citation_id=registry.next_id(),
                article_id=result.article_id,
                title=result.title,
                source_id=result.source_id,
                published_at=result.published_at,
                score=result.similarity_score,
                source_name=result.source_name,
                snippet=make_snippet(primary.text) if primary else None,
                chunk_id=primary.chunk_id if primary else None,
            )
            registry.add(item)
        evidence.append(item)
        parts = [_header(item.citation_id, result), *(c.text for c in sel.chunks)]
        blocks.append("\n".join(parts))

    return GroundedContext(text="\n\n".join(blocks), evidence=evidence, dropped_chunks=dropped)


def context_wrapper(excerpts: str) -> str:
    """Wrap the excerpts in ``<context>`` tags (placeholder when empty)."""
    return f"<context>\n{excerpts or NO_CONTEXT_PLACEHOLDER}\n</context>"


# ---------------------------------------------------------------------------
# Citation post-check
# ---------------------------------------------------------------------------


def sanitize_citations(text: str, valid_ids: Iterable[int]) -> tuple[str, list[int]]:
    """Strip ``[#n]`` markers that do not refer to a context block.

    Returns ``(clean_text, sorted_unique_invalid_ids)``. A grouped marker like
    ``[#1, #9]`` keeps only its valid ids (``[#1]``); a fully invalid marker is
    removed together with the space before it. General whitespace is left
    untouched so markdown layout survives.
    """
    valid = set(valid_ids)
    invalid: set[int] = set()

    def _replace(match: re.Match[str]) -> str:
        lead, body = match.group(1), match.group(2)
        ids = [int(n) for n in _NUMBER_RE.findall(body)]
        bad = [i for i in ids if i not in valid]
        if not bad:
            return match.group(0)
        invalid.update(bad)
        good = [i for i in ids if i in valid]
        if good:
            return lead + "[" + ", ".join(f"#{i}" for i in good) + "]"
        nxt = match.string[match.end() : match.end() + 1]
        # "word[#9]next" → keep the separator so words don't fuse.
        return lead if nxt.isalnum() else ""

    clean = _CITATION_RE.sub(_replace, text)
    return clean, sorted(invalid)
