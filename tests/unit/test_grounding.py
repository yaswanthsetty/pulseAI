import uuid

from backend.modules.agents.grounding import (
    build_context,
    estimate_tokens,
    sanitize_citations,
    truncate_to_tokens,
)
from backend.modules.retrieval.schemas import ChunkExcerpt, SearchResult


def test_token_utils():
    text = "Hello world"
    tokens = estimate_tokens(text)
    assert tokens > 0
    trunc = truncate_to_tokens(text, 1)
    assert trunc != text
    assert len(trunc) < len(text)


def test_sanitize_citations():
    text = "Here is a fact [#1, #2]. Another fact [#3]."
    clean, invalid = sanitize_citations(text, {1})
    assert clean == "Here is a fact [#1]. Another fact."
    assert invalid == [2, 3]


def test_build_context_budget():
    results = [
        SearchResult(
            article_id=uuid.UUID(int=1),
            title="Title 1",
            chunk_text="A very long text that takes up a lot of space." * 50,
            similarity_score=1.0,
            source_id=uuid.UUID(int=1),
        ),
        SearchResult(
            article_id=uuid.UUID(int=2),
            title="Title 2",
            chunk_text="Short text",
            similarity_score=0.9,
            source_id=uuid.UUID(int=2),
        ),
    ]

    # 1. Very small budget (truncates first chunk)
    ctx1 = build_context(results, token_budget=50)
    assert len(ctx1.evidence) == 1
    assert ctx1.evidence[0].article_id == uuid.UUID(int=1)
    assert "Title 1" in ctx1.text
    assert "Title 2" not in ctx1.text
    assert ctx1.dropped_chunks > 0

    # 2. Enough budget for both
    ctx2 = build_context(results, token_budget=2000)
    assert len(ctx2.evidence) == 2
    assert "Title 1" in ctx2.text
    assert "Title 2" in ctx2.text
    assert ctx2.dropped_chunks == 0


def test_build_context_max_chunks():
    results = [
        SearchResult(
            article_id=uuid.UUID(int=1),
            title="Title 1",
            chunk_text="Primary chunk.",
            similarity_score=1.0,
            source_id=uuid.UUID(int=1),
            extra_chunks=[
                ChunkExcerpt(
                    chunk_id=uuid.UUID(int=2), chunk_text="Secondary chunk.", chunk_index=1
                )
            ],
        )
    ]

    # max_chunks = 1
    ctx1 = build_context(results, token_budget=1000, max_chunks_per_article=1)
    assert "Primary chunk" in ctx1.text
    assert "Secondary chunk" not in ctx1.text

    # max_chunks = 2
    ctx2 = build_context(results, token_budget=1000, max_chunks_per_article=2)
    assert "Primary chunk" in ctx2.text
    assert "Secondary chunk" in ctx2.text
