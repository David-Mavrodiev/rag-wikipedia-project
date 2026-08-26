from __future__ import annotations

import logging
import sys
from pathlib import Path

from prefect import flow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


@flow(name="wikipedia-ingest")
def ingest_flow(profile: str | None = None, *, force: bool = False):
    """Ingest a Wikipedia profile into Qdrant. Resumable by default.

    Embedding is ~97% of ingestion time, and the `real` profile is ~15 hours of
    it on CPU. Deterministic point IDs already made the *upsert* idempotent, but
    a re-run still re-embedded every article from the start, so an interruption
    meant beginning again - which is why the local collection sat at 984 chunks
    instead of a complete run.

    Each article's point IDs are now checked before embedding. Chunks that are
    already stored are skipped, so a re-run costs one cheap lookup per article
    instead of the whole embedding bill, and an interrupted run continues from
    where it stopped.

    Pass ``force=True`` to re-embed and overwrite regardless - needed when the
    embedding model or the chunking parameters change, because the point IDs
    stay the same while the vectors they should hold do not.
    """
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.vectorstore import QdrantStore

    from pipeline.sources import iter_articles
    from pipeline.tasks import chunk_article, clean_article, embed_chunks, upsert_to_qdrant

    profile = profile or settings.profile
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    store.ensure_collection(dim=384)

    inserted_total = 0
    skipped_chunks = 0
    skipped_articles = 0
    seen_articles = 0

    for article in iter_articles(profile):
        seen_articles += 1
        cleaned = clean_article(article)
        if len(cleaned["text"]) < 100:
            continue
        chunks = chunk_article(cleaned)
        if not chunks:
            continue

        pending = chunks
        if not force:
            already = store.existing_ids([chunk.point_id for chunk in chunks])
            if already:
                pending = [chunk for chunk in chunks if chunk.point_id not in already]
                skipped_chunks += len(already)
            if not pending:
                # Every chunk of this article is already stored: skip the
                # embedding entirely. This is the whole point of the check.
                skipped_articles += 1
                continue

        chunk_vectors = embed_chunks(pending, embedder)
        inserted_total += upsert_to_qdrant(chunk_vectors, store, cleaned)

    logger.info(
        "Ingestion complete: %s chunks upserted, %s chunks already present "
        "(%s articles skipped entirely of %s seen)",
        inserted_total,
        skipped_chunks,
        skipped_articles,
        seen_articles,
    )
    return inserted_total


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a Wikipedia profile into Qdrant.")
    parser.add_argument("--profile", default=None, help="tiny | real (default: PROFILE env)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-embed and overwrite chunks that are already stored. Needed when "
        "the embedding model or chunk size changes, since the point IDs do not.",
    )
    args = parser.parse_args()
    ingest_flow(args.profile, force=args.force)
