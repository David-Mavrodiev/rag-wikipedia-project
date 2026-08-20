from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

EXPECTED_DIM = 384


class QdrantStore:
    def __init__(self, url: str, collection: str):
        # generous timeout: bulk upserts under I/O load exceed the client's 5s default
        self._client = QdrantClient(url=url, timeout=60)
        self._collection = collection

    def ensure_collection(self, dim: int = EXPECTED_DIM) -> None:
        if dim != EXPECTED_DIM:
            raise ValueError(
                f"Expected embedding dim={EXPECTED_DIM}, got {dim}. Check EMBED_MODEL config."
            )
        existing = [collection.name for collection in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created collection '%s' dim=%s", self._collection, dim)

    def upsert_batch(self, points: list[dict[str, Any]]) -> None:
        structs = [
            PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
            for point in points
        ]
        self._client.upsert(collection_name=self._collection, points=structs)

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        # query_points replaced the removed search() in qdrant-client >= 1.12
        results = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        ).points
        return [
            {
                "score": result.score,
                "text": result.payload.get("text", ""),
                "title": result.payload.get("title", ""),
                "source_id": result.payload.get("source_id", ""),
            }
            for result in results
        ]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count
