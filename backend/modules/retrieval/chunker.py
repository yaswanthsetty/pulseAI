"""Sentence-aware, token-bounded text chunking (FR-8).

Chunks are bounded by an approximate token budget (BGE context window) while
never splitting mid-sentence:

* Empty/whitespace-only text yields no chunks.
* Text that fits inside the budget stays a single chunk (short articles are
  not needlessly split).
* A single sentence longer than the budget is hard-split on word boundaries
  (there is no smaller natural unit to respect).

Token counting is a deterministic whitespace-based approximation
(``~1.3 tokens per word``, matching typical BPE behavior) — no tokenizer
dependency is required for chunk *boundaries*.
"""

import re

DEFAULT_MAX_TOKENS = 512  # BGE context window; keep chunks comfortably inside it
_TOKENS_PER_WORD = 1.3

# Sentence boundary: punctuation followed by whitespace and an uppercase
# letter / digit / quote (avoids splitting on abbreviations like "U.S.").
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])")
_WHITESPACE = re.compile(r"\s+")


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` (1.3 tokens per word)."""
    if not text or not text.strip():
        return 0
    return max(1, round(len(text.split()) * _TOKENS_PER_WORD))


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on punctuation boundaries."""
    normalized = _WHITESPACE.sub(" ", text).strip()
    if not normalized:
        return []
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(normalized) if part.strip()]


def chunk_text(text: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """Chunk ``text`` into sentence-aligned, token-bounded pieces (FR-8).

    Returns a list of chunk strings, each with ``estimate_tokens(chunk) <=
    max_tokens``. The result is deterministic and idempotent.
    """
    normalized = _WHITESPACE.sub(" ", text or "").strip()
    if not normalized:
        return []
    if estimate_tokens(normalized) <= max_tokens:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    # Float accumulation (words * tokens-per-word) so per-piece rounding never
    # lets a chunk's total exceed the budget (``estimate_tokens`` rounds).
    current_tokens = 0.0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(" ".join(current))
        current, current_tokens = [], 0.0

    for sentence in split_sentences(normalized):
        sentence_tokens = len(sentence.split()) * _TOKENS_PER_WORD
        if sentence_tokens > max_tokens:
            # No sentence boundary to respect — hard-split on word boundaries.
            flush()
            words = sentence.split()
            buffer: list[str] = []
            buffer_tokens = 0.0
            for word in words:
                if buffer and buffer_tokens + _TOKENS_PER_WORD > max_tokens:
                    chunks.append(" ".join(buffer))
                    buffer, buffer_tokens = [], 0.0
                buffer.append(word)
                buffer_tokens += _TOKENS_PER_WORD
            if buffer:
                chunks.append(" ".join(buffer))
            continue

        if current and current_tokens + sentence_tokens > max_tokens:
            flush()
        current.append(sentence)
        current_tokens += sentence_tokens

    flush()
    return chunks
