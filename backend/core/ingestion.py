import feedparser
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import time
import asyncio

from backend.db.models import Source, Article
from backend.schemas.article import ArticleCreate

# Seed list of high-quality tech/global news sources matching our roadmap
DEFAULT_SOURCES = [
    {"name": "TechCrunch AI", "base_url": "https://techcrunch.com/category/artificial-intelligence/feed/", "feed_type": "RSS", "credibility_score": 0.85},
    {"name": "Reuters Technology", "base_url": "https://newsfeed.reuters.com/reuters/technologyNews", "feed_type": "RSS", "credibility_score": 1.00},
    {"name": "BBC Science & Tech", "base_url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "feed_type": "RSS", "credibility_score": 0.95}
]

def seed_default_sources(db: Session):
    """Ensures primary data streams exist inside the relational metadata ledger."""
    for src_data in DEFAULT_SOURCES:
        existing = db.query(Source).filter(Source.name == src_data["name"]).first()
        if not existing:
            new_source = Source(**src_data)
            db.add(new_source)
    db.commit()

def clean_html_payload(raw_html: str) -> str:
    """Strips HTML markup fragments to extract plain body strings for future embedding steps."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ").strip()

def parse_rss_feed(source: Source, db: Session) -> int:
    """Polls an RSS feed, tracks duplicates, and commits fresh records."""
    feed = feedparser.parse(source.base_url)
    new_articles_count = 0

    for entry in feed.entries:
        # Avoid insertion errors by verifying uniqueness upfront
        existing_article = db.query(Article).filter(Article.url == entry.link).first()
        if existing_article:
            continue

        # Extract textual summary or full description fields safely
        raw_body = getattr(entry, "summary", "") or getattr(entry, "description", "")
        clean_body = clean_html_payload(raw_body)
        
        if not clean_body:
            clean_body = "No text content provided in source metadata summary."

        # Standardize publishing dates across different feed structures
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
        else:
            pub_date = datetime.now(timezone.utc)

        author_name = getattr(entry, "author", "Unknown Outlet Correspondent")

        try:
            db_article = Article(
                source_id=source.id,
                title=entry.title,
                url=entry.link,
                author=author_name,
                body_text=clean_body,
                published_at=pub_date
            )
            db.add(db_article)
            db.commit()
            new_articles_count += 1
        except Exception as e:
            db.rollback()
            print(f"Skipping entry {entry.title} due to structural error: {e}")

    return new_articles_count