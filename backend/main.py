from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import text
import httpx

from backend.db.database import get_db
from backend.core.config import settings

app = FastAPI(
    title="PulseAI — Real-Time News Intelligence Engine",
    description="Production-grade asynchronous ingestion and temporal retrieval API framework.",
    version="1.0.0"
)

@app.on_event("startup")
async def verify_infrastructure_connections():
    """
    Validates connection readiness to both storage layers on application spin-up.
    Ensures clear failure visibility before data ingestion workers begin cycles.
    """
    print("\n=== Investigating System Infrastructure Dependencies ===")
    
    # 1. Validate Relational Base (PostgreSQL)
    try:
        db_generator = get_db()
        db_session = next(db_generator)
        db_session.execute(text("SELECT 1"))
        print(" -> PostgreSQL Connection State: OPERATIONAL")
    except Exception as e:
        print(f" -> PostgreSQL Connection State: FAILED. Error details: {e}")
        
    # 2. Validate Vector Engine Rest Layer (Qdrant)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{settings.QDRANT_URL}/healthz", timeout=3.0)
            if response.status_code == 200:
                print(" -> Qdrant Vector DB State: OPERATIONAL")
            else:
                print(f" -> Qdrant Vector DB State: UNEXPECTED STATUS ({response.status_code})")
        except Exception as e:
            print(f" -> Qdrant Vector DB State: UNREACHABLE. Error details: {e}")
            
    print("========================================================\n")


@app.get("/health", status_code=status.HTTP_200_OK)
def system_health_check(db: Session = Depends(get_db)):
    """
    Operational endpoint for automated health checks and platform uptime verification.
    """
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
            "engine": "PulseAI Core Ready"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database liveness check failed: {str(e)}"
        )