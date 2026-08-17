"""Unit tests for the sentence-aware token-bounded chunker (FR-8 / spec §15)."""

from backend.modules.retrieval.chunker import (
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_SINGLE_CHUNK_MAX_TOKENS,
    DEFAULT_TARGET_TOKENS,
    chunk_text,
    estimate_tokens,
    split_sentences,
)


class TestEstimateTokens:
    def test_empty_text(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("   ") == 0

    def test_counts_words_approximately(self):
        assert estimate_tokens("one two three") == 4  # round(3 * 1.3)


class TestSplitSentences:
    def test_splits_on_sentence_boundaries(self):
        text = "First sentence here. Second one! And a third?"
        sentences = split_sentences(text)
        assert sentences == ["First sentence here.", "Second one!", "And a third?"]

    def test_keeps_abbreviations_intact(self):
        text = "The U.S. economy grew. GDP rose."
        assert split_sentences(text) == ["The U.S. economy grew.", "GDP rose."]

    def test_empty_text(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []


class TestChunkText:
    def test_empty_text_yields_no_chunks(self):
        assert chunk_text("") == []
        assert chunk_text("   \n  ") == []

    def test_short_text_stays_single_chunk(self):
        text = "A short article that easily fits inside the budget."
        assert chunk_text(text) == [text]

    def test_article_between_target_and_single_chunk_threshold_stays_single(self):
        # ~215 words ≈ 280 tokens: over the 256 target, under the 300
        # single-chunk threshold (spec §15: no forced multi-chunking).
        text = " ".join("word" for _ in range(215))
        assert chunk_text(text) == [text]

    def test_long_article_splits_into_bounded_sentence_chunks(self):
        text = " ".join(
            f"Sentence number {i} discusses a distinct topic with enough words to matter."
            for i in range(60)
        )
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(estimate_tokens(c) <= DEFAULT_TARGET_TOKENS for c in chunks)
        # Every chunk is made of complete sentences (each starts with "Sentence").
        assert all(c.startswith("Sentence number") for c in chunks)
        # Original words are preserved across chunk boundaries.
        assert set(" ".join(chunks).split()) == set(text.split())

    def test_consecutive_chunks_overlap_sentence_aligned(self):
        text = " ".join(
            f"Sentence {i} describes a distinct topic with enough words to matter."
            for i in range(60)
        )
        chunks = chunk_text(text)
        assert len(chunks) > 1
        for prev, nxt in zip(chunks, chunks[1:], strict=False):
            prev_sentences = split_sentences(prev)
            nxt_sentences = split_sentences(nxt)
            # The next chunk starts with trailing sentences of the previous one,
            # in the same order (sentence-aligned overlap).
            assert nxt_sentences[0] in prev_sentences
            shared = len(nxt_sentences)
            while shared and nxt_sentences[:shared] != prev_sentences[-shared:]:
                shared -= 1
            assert shared >= 1
            overlap = " ".join(nxt_sentences[:shared])
            assert estimate_tokens(overlap) <= DEFAULT_OVERLAP_TOKENS

    def test_overlap_is_respected(self):
        text = " ".join(f"Sentence {i} about a topic of interest to readers." for i in range(60))
        no_overlap = chunk_text(text, overlap_tokens=0)
        with_overlap = chunk_text(text, overlap_tokens=DEFAULT_OVERLAP_TOKENS)
        # Sharing sentences between chunks means more chunks cover the same text.
        assert len(with_overlap) >= len(no_overlap)

    def test_oversized_sentence_is_hard_split(self):
        text = " ".join(["word"] * 2000)  # ~2600 tokens in one sentence
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(estimate_tokens(c) <= DEFAULT_TARGET_TOKENS for c in chunks)
        assert all(c.split() == ["word"] * len(c.split()) for c in chunks)

    def test_respects_custom_budget(self):
        text = " ".join(f"Sentence {i} has a few words in it." for i in range(40))
        chunks = chunk_text(text, target_tokens=100, overlap_tokens=20)
        assert all(estimate_tokens(c) <= 100 for c in chunks)
        assert len(chunks) > len(chunk_text(text))

    def test_deterministic(self):
        text = " ".join(f"Sentence {i} about a topic of interest to readers." for i in range(50))
        assert chunk_text(text) == chunk_text(text)

    def test_single_chunk_threshold_constant_sane(self):
        # The single-chunk threshold sits above the per-chunk target (§15).
        assert DEFAULT_SINGLE_CHUNK_MAX_TOKENS > DEFAULT_TARGET_TOKENS
