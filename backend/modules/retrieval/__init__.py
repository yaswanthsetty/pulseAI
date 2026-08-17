"""Retrieval module — Phase 2 (FR-8..FR-13).

Semantic search over Qdrant (BGE-small dense vectors, cosine) is available as
``POST /api/v1/search``, and the embedding pipeline (sentence-aware chunking
FR-8, BGE dense embeddings FR-9, async ``embed`` queue FR-10) populates the
``pulseai_articles`` collection from the worker. Hybrid dense+sparse
retrieval, cross-encoder reranking, and temporal ranking land in Phase 4.
"""
