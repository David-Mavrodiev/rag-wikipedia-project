from __future__ import annotations

import logging
import re
from typing import Any

from app.core.chunking import Chunk, chunk_text
from prefect import task
from prefect.cache_policies import NO_CACHE

logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    """Basic Wikipedia markup cleanup."""
    text = re.sub(r"={2,}[^=]+=+", " ", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


@task(name="clean-article")
def clean_article(article: dict) -> dict:
    return {**article, "text": clean_text(article["text"])}


@task(name="chunk-article")
def chunk_article(article: dict) -> list[Chunk]:
    return chunk_text(article["text"], source_id=article["id"])


@task(name="embed-chunks", cache_policy=NO_CACHE)
def embed_chunks(chunks: list[Chunk], embedder: Any) -> list[tuple[Chunk, list[float]]]:
    texts = [chunk.text for chunk in chunks]
    vectors = embedder.embed_batch(texts)
    return list(zip(chunks, vectors))


@task(name="upsert-to-qdrant", cache_policy=NO_CACHE)
def upsert_to_qdrant(
    chunk_vectors: list[tuple[Chunk, list[float]]],
    vectorstore: Any,
    article: dict,
) -> int:
    points = []
    for chunk, vector in chunk_vectors:
        points.append(
            {
                "id": chunk.point_id,
                "vector": vector,
                "payload": {
                    "text": chunk.text,
                    "source_id": chunk.source_id,
                    "chunk_index": chunk.chunk_index,
                    "title": article["title"],
                },
            }
        )
    vectorstore.upsert_batch(points)
    return len(points)


# --- batched path -----------------------------------------------------------
#
# The per-article path costs four Prefect task runs, one Qdrant existence check
# and one upsert round-trip PER ARTICLE. That is tolerable while articles are
# long, but chunk density falls sharply with stream depth - measured 11.1
# chunks/article over the first 2,000, and 2.4 by article 12,000. At ~2 chunks
# an article the fixed cost dominates completely and throughput collapsed from
# 24 to 1 chunk/s.
#
# Batching amortises all three over a whole segment: one existence check, one
# embed call with a real batch, and upserts in blocks.

def prepare_article(article: dict) -> tuple[dict, list[Chunk]] | None:
    """Clean and chunk one article. None when it yields nothing worth storing."""
    cleaned = {**article, "text": clean_text(article["text"])}
    if len(cleaned["text"]) < 100:
        return None
    chunks = chunk_text(cleaned["text"], source_id=cleaned["id"])
    return (cleaned, chunks) if chunks else None


@task(name="ingest-segment", cache_policy=NO_CACHE)
def ingest_segment(
    segment: list[tuple[dict, list[Chunk]]],
    vectorstore: Any,
    embedder: Any,
    *,
    force: bool = False,
    embed_batch_size: int = 64,
    upsert_batch_size: int = 256,
) -> tuple[int, int]:
    """Ingest a whole segment. Returns (chunks upserted, chunks already present)."""
    pairs = [(article, chunk) for article, chunks in segment for chunk in chunks]
    if not pairs:
        return 0, 0

    already = 0
    if not force:
        # ONE existence check for the segment instead of one per article.
        present = vectorstore.existing_ids([chunk.point_id for _, chunk in pairs])
        if present:
            already = len(present)
            pairs = [(a, c) for a, c in pairs if c.point_id not in present]
    if not pairs:
        return 0, already

    vectors = embedder.embed_batch(
        [chunk.text for _, chunk in pairs], batch_size=embed_batch_size
    )

    points = [
        {
            "id": chunk.point_id,
            "vector": vector,
            "payload": {
                "text": chunk.text,
                "source_id": chunk.source_id,
                "chunk_index": chunk.chunk_index,
                "title": article["title"],
            },
        }
        for (article, chunk), vector in zip(pairs, vectors)
    ]
    # Upsert in blocks: one request with several thousand points is a large
    # payload and a long-held connection.
    for offset in range(0, len(points), upsert_batch_size):
        vectorstore.upsert_batch(points[offset : offset + upsert_batch_size])
    return len(points), already
