from __future__ import annotations

import logging
import re
from typing import Any

from app.core.chunking import Chunk, chunk_text
from prefect import task

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


@task(name="embed-chunks")
def embed_chunks(chunks: list[Chunk], embedder: Any) -> list[tuple[Chunk, list[float]]]:
    texts = [chunk.text for chunk in chunks]
    vectors = embedder.embed_batch(texts)
    return list(zip(chunks, vectors))


@task(name="upsert-to-qdrant")
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
