"""Ingestion module: source polling, feed parsing, dedupe, and processing.

Owns FR-1..FR-7. Exposes a service layer for the API router and RQ jobs;
the module must not import from sibling business modules (see .importlinter).
"""
