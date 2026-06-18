import asyncio
import httpx

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import text
from backend.core.embeddings import ensure_qdrant_collection, vectorize_new_articles
from backend.db.database import get_db, SessionLocal
from backend.core.config import settings
from backend.core.ingestion import (
    seed_default_sources,
    parse_rss_feed,
)
from backend.db.models import Source


# ------------------------------------------------------------------
# Infrastructure Validation
# ------------------------------------------------------------------

async def verify_infrastructure_connections():
    """
    Validate PostgreSQL and Qdrant before application startup completes.
    """

    print("\n=== Investigating System Infrastructure Dependencies ===")

    # PostgreSQL Check
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        print(" -> PostgreSQL Connection State: OPERATIONAL")
    except Exception as e:
        print(f" -> PostgreSQL Connection State: FAILED. Error details: {e}")
    finally:
        db.close()

    # Qdrant Check
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.QDRANT_URL}/healthz",
                timeout=3.0,
            )

            if response.status_code == 200:
                print(" -> Qdrant Vector DB State: OPERATIONAL")
            else:
                print(
                    f" -> Qdrant Vector DB State: "
                    f"UNEXPECTED STATUS ({response.status_code})"
                )

    except Exception as e:
        print(
            f" -> Qdrant Vector DB State: "
            f"UNREACHABLE. Error details: {e}"
        )

    print("========================================================\n")

# ------------------------------------------------------------------
# Background RSS Ingestion & Vectorization Worker
# ------------------------------------------------------------------

async def background_ingestion_loop():
    """
    Continuous RSS synchronization and AI embedding worker.
    """
    try:
        while True:
            print(
                "[Ingestion Engine] Beginning continuous news sync cycle..."
            )

            db = SessionLocal()

            try:
                # --- 1. DATA INGESTION ---
                active_sources = (
                    db.query(Source)
                    .filter(Source.is_active == True)
                    .all()
                )

                total_added = 0

                for source in active_sources:
                    try:
                        added = await asyncio.to_thread(
                            parse_rss_feed,
                            source,
                            db
                        )
                        total_added += added
                        print(
                            f" -> Synchronized '{source.name}': "
                            f"Ingested {added} new articles."
                        )
                    except Exception as source_error:
                        print(
                            f" -> Failed syncing source "
                            f"'{source.name}': {source_error}"
                        )

                print(
                    "[Ingestion Engine] Cycle finalized. "
                    f"Total rows committed: {total_added}"
                )

                # --- 2. AI VECTOR EMBEDDING ---
                try:
                    # Run blocking Qdrant setup and embedding generation in background threads
                    await asyncio.to_thread(ensure_qdrant_collection)
                    embedded_count = await asyncio.to_thread(vectorize_new_articles, db)
                    
                    if embedded_count > 0:
                        print(
                            f"[AI Engine] Successfully embedded and synced "
                            f"{embedded_count} articles to Qdrant."
                        )
                except Exception as ai_error:
                    print(
                        f"[AI Engine Warning] Failed to embed batch: {ai_error}"
                    )

            except Exception as e:
                print(
                    "[Ingestion Engine Severe Warning] "
                    f"Synchronization error encountered: {e}"
                )

            finally:
                db.close()

            # Poll every 5 minutes
            await asyncio.sleep(300)

    except asyncio.CancelledError:
        print("[Worker] Shutdown signal received.")
        raise

# ------------------------------------------------------------------
# Application Lifespan
# ------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(app: FastAPI):

    # Validate infrastructure
    await verify_infrastructure_connections()

    # Seed RSS sources
    db = SessionLocal()

    try:
        seed_default_sources(db)
        print("[Startup] RSS source seeding completed.")
    finally:
        db.close()

    # Start background worker
    ingestion_task = asyncio.create_task(
        background_ingestion_loop()
    )

    print("[Startup] Ingestion worker started.")

    try:
        yield

    finally:
        print("[Shutdown] Stopping ingestion worker...")

        ingestion_task.cancel()

        try:
            await ingestion_task
        except asyncio.CancelledError:
            pass

        print("[Shutdown] Ingestion worker stopped.")


# ------------------------------------------------------------------
# FastAPI Application
# ------------------------------------------------------------------

app = FastAPI(
    title="PulseAI — Real-Time News Intelligence Engine",
    description=(
        "Production-grade asynchronous ingestion and "
        "temporal retrieval API framework."
    ),
    version="1.0.0",
    lifespan=app_lifespan,
)


# ------------------------------------------------------------------
# Health Endpoint
# ------------------------------------------------------------------

@app.get("/health", status_code=status.HTTP_200_OK)
def system_health_check(
    db: Session = Depends(get_db)
):
    """
    Operational endpoint for uptime and liveness monitoring.
    """

    try:
        db.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": "connected",
            "engine": "PulseAI Core Ready",
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database liveness check failed: {str(e)}",
        )