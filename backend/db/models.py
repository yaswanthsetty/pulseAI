from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.db.database import Base

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    base_url = Column(String(255), nullable=False)
    feed_type = Column(String(20), nullable=False)  # 'RSS', 'API'
    credibility_score = Column(Numeric(3, 2), default=0.50)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to articles
    articles = relationship("Article", back_populates="source")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    confidence_score = Column(Numeric(3, 2), nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship to articles mapped to this event
    articles = relationship("Article", back_populates="event")


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)  # Populated in Phase 4
    title = Column(Text, nullable=False)
    url = Column(Text, unique=True, nullable=False)
    author = Column(String(255), nullable=True)
    body_text = Column(Text, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    qdrant_point_id = Column(UUID(as_uuid=True), nullable=True)  # Links to Vector DB

    # Relationships
    source = relationship("Source", back_populates="articles")
    event = relationship("Event", back_populates="articles")