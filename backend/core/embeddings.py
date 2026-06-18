import uuid
from typing import List
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import Article, Source

# 1. Initialize the Embedding Model
# We use BGE-small because it is incredibly fast, ranks very high on the MTEB leaderboard,
# and outputs a lightweight 384-dimensional vector. Perfect for real-time news.
print("[AI Engine] Loading BAAI/bge-small-en-v1.5 into memory...")
embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")

# 2. Initialize Qdrant Client
qdrant = QdrantClient(url=settings.QDRANT_URL)
COLLECTION_NAME = "pulseai_articles"

def ensure_qdrant_collection():
    """Creates the vector collection if it does not exist."""
    collections = qdrant.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        print(f"[Vector DB] Creating new collection: {COLLECTION_NAME}")
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=384,  # BGE-small output dimension
                distance=Distance.COSINE
            )
        )

def vectorize_new_articles(db: Session) -> int:
    """
    Finds articles without a vector ID, embeds them, uploads them to Qdrant with metadata,
    and updates the PostgreSQL row with the corresponding Qdrant UUID.
    """
    # Find articles that haven't been sent to Qdrant yet
    unprocessed = db.query(Article).filter(Article.qdrant_point_id == None).limit(100).all()
    
    if not unprocessed:
        return 0

    points_to_upsert = []
    db_updates = []

    for article in unprocessed:
        # 1. Create a unique ID for Qdrant
        vector_id = str(uuid.uuid4())
        
        # 2. Prepare the text (Combining title and body for maximum context)
        text_to_embed = f"{article.title}. {article.body_text}"
        
        # 3. Generate the 384-dimensional vector
        # The model natively handles truncation to 512 tokens
        embedding = embedder.encode(text_to_embed).tolist()
        
        # 4. Attach crucial Metadata (This is vital for the Temporal/Credibility Math later)
        # We store timestamps as integers (Unix time) because Qdrant filters numbers extremely fast
        payload = {
            "article_id": article.id,
            "source_id": article.source_id,
            "credibility_score": float(article.source.credibility_score) if article.source else 0.5,
            "published_at_unix": int(article.published_at.timestamp()),
            "title": article.title
        }
        
        # 5. Create the Qdrant Point structure
        points_to_upsert.append(PointStruct(id=vector_id, vector=embedding, payload=payload))
        db_updates.append((article, vector_id))

    # Batch upsert to Qdrant (Much faster than doing it one by one)
    try:
        qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=points_to_upsert
        )
        
        # If Qdrant succeeds, update PostgreSQL with the generated UUIDs
        for article, v_id in db_updates:
            article.qdrant_point_id = v_id
            
        db.commit()
        return len(points_to_upsert)
        
    except Exception as e:
        db.rollback()
        print(f"[Vector DB Error] Failed to upsert batch: {e}")
        return 0