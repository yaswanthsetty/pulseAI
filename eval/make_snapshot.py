"""Freeze the corpus into a snapshot so eval results stay comparable.

Usage:
    uv run python -m eval.make_snapshot

Writes ``eval/snapshots/<yyyymmdd>_<gitsha7>.json`` containing every article
(id, source, dates, extractor fields, quality) and its ordered chunk ids with
token counts, plus a Qdrant-vs-DB consistency check. The snapshot id is
embedded in every results file produced against it (plan §1.1).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from backend.core.database import SessionLocal
from backend.db.models import Article, ArticleChunk, Source
from backend.modules.retrieval import service as retrieval
from sqlalchemy import select

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _git_sha7() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 - snapshots must still work outside git
        return "nogit"


def _qdrant_ids() -> set[str]:
    client = retrieval.get_qdrant_client()
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=retrieval.COLLECTION_NAME,
            limit=1000,
            with_payload=False,
            offset=offset,
        )
        ids.update(str(p.id) for p in points)
        if offset is None:
            return ids


def build_snapshot() -> dict:
    with SessionLocal() as db:
        sources = {s.id: s.name for s in db.execute(select(Source)).scalars()}
        articles = list(
            db.execute(select(Article).order_by(Article.published_at, Article.id)).scalars()
        )
        chunks = list(db.execute(select(ArticleChunk)).scalars())

    by_article: dict[str, list[dict]] = {}
    for c in chunks:
        by_article.setdefault(str(c.article_id), []).append(
            {
                "chunk_id": str(c.id),
                "chunk_number": c.chunk_number,
                "token_count": c.token_count,
                "qdrant_point_id": str(c.qdrant_point_id) if c.qdrant_point_id else None,
                "embedding_status": c.embedding_status,
            }
        )
    for rows in by_article.values():
        rows.sort(key=lambda r: r["chunk_number"])

    db_point_ids = {
        r["qdrant_point_id"] for rows in by_article.values() for r in rows if r["qdrant_point_id"]
    }
    qdrant_ids = _qdrant_ids()

    article_records = []
    for a in articles:
        rows = by_article.get(str(a.id), [])
        article_records.append(
            {
                "article_id": str(a.id),
                "source_id": str(a.source_id),
                "source_name": sources.get(a.source_id),
                "title": a.title,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "processed_at": a.processed_at.isoformat() if a.processed_at else None,
                "language_code": a.language_code,
                "category_code": a.category_code,
                "extractor": a.extractor,
                "extractor_version": a.extractor_version,
                "extraction_quality": a.extraction_quality,
                "chunk_ids": [r["chunk_id"] for r in rows],
                "chunks": rows,
            }
        )

    return {
        "snapshot_id": f"{dt.date.today().strftime('%Y%m%d')}_{_git_sha7()}",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "git_sha": _git_sha7(),
        "counts": {
            "articles": len(article_records),
            "chunks": len(chunks),
            "embedded_chunks": sum(1 for c in chunks if c.embedding_status == "embedded"),
        },
        "consistency": {
            "db_point_ids": len(db_point_ids),
            "qdrant_points": len(qdrant_ids),
            "missing_in_qdrant": sorted(db_point_ids - qdrant_ids),
            "orphaned_in_qdrant": sorted(qdrant_ids - db_point_ids),
            "consistent": db_point_ids == qdrant_ids,
        },
        "extractor_mix": _mix(article_records, "extractor"),
        "quality_mix": _mix(article_records, "extraction_quality"),
        "articles": article_records,
    }


def _mix(records: list[dict], key: str) -> dict[str, int]:
    mix: dict[str, int] = {}
    for r in records:
        mix[str(r[key])] = mix.get(str(r[key]), 0) + 1
    return dict(sorted(mix.items()))


def main() -> int:
    snap = build_snapshot()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{snap['snapshot_id']}.json"
    if path.exists():
        print(f"snapshot already exists (refusing to overwrite): {path}")
        return 1
    path.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    c = snap["counts"]
    k = snap["consistency"]
    print(f"snapshot_id : {snap['snapshot_id']}")
    print(f"articles    : {c['articles']}  chunks: {c['chunks']}  embedded: {c['embedded_chunks']}")
    print(
        f"qdrant      : {k['qdrant_points']} points, db point ids: {k['db_point_ids']}, "
        f"consistent: {k['consistent']}"
    )
    print(f"extractor   : {snap['extractor_mix']}")
    print(f"quality     : {snap['quality_mix']}")
    print(f"written     : {path}")
    return 0 if k["consistent"] else 2


if __name__ == "__main__":
    sys.exit(main())
