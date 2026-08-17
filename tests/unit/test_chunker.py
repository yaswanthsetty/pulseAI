"""Unit tests for the sentence-aware token-bounded chunker (FR-8)."""

from backend.modules.retrieval.chunker import (
    DEFAULT_MAX_TOKENS,
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

    def test_long_article_splits_into_bounded_sentence_chunks(self):
        # ~30 sentences, each ~20 words (~26 tokens) → 6+ chunks of ≤ 512 tokens.
        text = " ".join(
            f"Sentence number {i} discusses a distinct topic with enough words to matter." * 1
            for i in range(60)
        )
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(estimate_tokens(c) <= DEFAULT_MAX_TOKENS for c in chunks)
        # Every chunk is made of complete sentences (each starts with "Sentence").
        assert all(c.startswith("Sentence number") for c in chunks)
        # Original words are preserved (no loss across chunk boundaries).
        joined = " ".join(chunks)
        assert set(joined.split()) == set(text.split())

    def test_oversized_sentence_is_hard_split(self):
        text = " ".join(["word"] * 2000)  # ~2600 tokens in one sentence
        chunks = chunk_text(text)
        assert len(chunks) > 1
        assert all(estimate_tokens(c) <= DEFAULT_MAX_TOKENS for c in chunks)
        assert all(c.split() == ["word"] * len(c.split()) for c in chunks)

    def test_respects_custom_budget(self):
        text = " ".join(f"Sentence {i} has a few words in it." for i in range(40))
        chunks = chunk_text(text, max_tokens=100)
        assert all(estimate_tokens(c) <= 100 for c in chunks)
        assert len(chunks) > len(chunk_text(text, max_tokens=512))

    def test_deterministic(self):
        text = " ".join(f"Sentence {i} about a topic of interest to readers." for i in range(50))
        assert chunk_text(text) == chunk_text(text)
