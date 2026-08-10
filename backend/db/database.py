from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import settings

# The sync engine always uses a sync driver, even when ``POSTGRES_DRIVER`` is
# set to ``postgresql+asyncpg`` (async support lands with Phase 2 async code).
_sync_url = settings.database_url.replace("postgresql+asyncpg", "postgresql")

engine = create_engine(_sync_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
