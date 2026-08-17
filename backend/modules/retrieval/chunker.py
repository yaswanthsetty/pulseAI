"""Sentence-aware, token-bounded text chunking (FR-8 / spec §15).

Spec parameters, implemented exactly:

* **Target 256 tokens per chunk** — a new chunk starts once adding the next
  sentence would exceed the budget, so chunks never exceed it.
* **40-token overlap** — consecutive chunks share the trailing sentences of
  the previous chunk up to ~40 tokens (sentence-aligned, never mid-sentence).
* **Short articles (<300 tokens, the majority of news) stay a single chunk**
  — no forced multi-chunking of short content.

A single sentence longer than the target is hard-split on word boundaries
(there is no smaller natural unit to respect); such word-level splits carry
no overlap.

Token counting is a deterministic whitespace-based approximation
(``~1.3 tokens per word``, matching typical BPE behavior) — no tokenizer
dependency is required for chunk *boundaries*.
"""

import re

DEFAULT_TARGET_TOKENS = 256  # spec §15 / FR-8
DEFAULT_OVERLAP_TOKENS = 40  # spec §15 / FR-8
DEFAULT_SINGLE_CHUNK_MAX_TOKENS = 300  # spec §15: short articles single-chunk
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


def _word_tokens(text: str) -> float:
    """Exact (non-rounded) token estimate for budget accumulation."""
    return len(text.split()) * _TOKENS_PER_WORD


def chunk_text(
    text: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    single_chunk_max_tokens: int = DEFAULT_SINGLE_CHUNK_MAX_TOKENS,
) -> list[str]:
    """Chunk ``text`` per spec §15 (sentence-aligned, token-bounded, overlap).

    Returns a list of chunk strings, each with ``estimate_tokens(chunk) <=
    target_tokens`` (a short article under ``single_chunk_max_tokens`` is a
    single chunk even if it exceeds the target slightly). Deterministic.
    """
    normalized = _WHITESPACE.sub(" ", text or "").strip()
    if not normalized:
        return []
    if estimate_tokens(normalized) <= single_chunk_max_tokens:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0.0

    def flush() -> None:
        """Emit the current chunk and seed the next one with its overlap tail."""
        nonlocal current, current_tokens
        if not current:
            return
        chunks.append(" ".join(current))
        # Trailing sentences of this chunk (cumulatively <= overlap_tokens) seed
        # the next chunk, keeping the overlap sentence-aligned and bounded.
        seed: list[str] = []
        seed_tokens = 0.0
        for sentence in reversed(current):
            tokens = _word_tokens(sentence)
            if seed_tokens + tokens > overlap_tokens:
                break
            seed.insert(0, sentence)
            seed_tokens += tokens
        current, current_tokens = seed, seed_tokens

    for sentence in split_sentences(normalized):
        tokens = _word_tokens(sentence)
        if tokens > target_tokens:
            # No sentence boundary to respect — hard-split on word boundaries.
            flush()
            words = sentence.split()
            buffer: list[str] = []
            buffer_tokens = 0.0
            for word in words:
                if buffer and buffer_tokens + _TOKENS_PER_WORD > target_tokens:
                    chunks.append(" ".join(buffer))
                    buffer, buffer_tokens = [], 0.0
                buffer.append(word)
                buffer_tokens += _TOKENS_PER_WORD
            if buffer:
                chunks.append(" ".join(buffer))
            continue

        if current and current_tokens + tokens > target_tokens:
            flush()
        current.append(sentence)
        current_tokens += tokens

    flush()
    return chunks
