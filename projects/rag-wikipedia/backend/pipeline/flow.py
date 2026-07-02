from __future__ import annotations

import logging
import sys
from pathlib import Path

from prefect import flow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


@flow(name="wikipedia-ingest")
def ingest_flow(profile: str | None = None):
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.vectorstore import QdrantStore

    from pipeline.sources import iter_articles
    from pipeline.tasks import chunk_article, clean_article, embed_chunks, upsert_to_qdrant

    profile = profile or settings.profile
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    store.ensure_collection(dim=384)

    total = 0
    for article in iter_articles(profile):
        cleaned = clean_article(article)
        if len(cleaned["text"]) < 100:
            continue
        chunks = chunk_article(cleaned)
        if not chunks:
            continue
        chunk_vectors = embed_chunks(chunks, embedder)
        inserted = upsert_to_qdrant(chunk_vectors, store, cleaned)
        total += inserted

    logger.info("Ingestion complete: %s chunks upserted", total)
    return total


if __name__ == "__main__":
    ingest_flow()
