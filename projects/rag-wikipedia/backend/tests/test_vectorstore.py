"""Contract tests against the real qdrant-client API surface.

These use an in-memory Qdrant (":memory:") rather than mocks: mocking the client
is what let the removal of QdrantClient.search() reach production unnoticed.
"""

from __future__ import annotations

import pytest
from app.core.vectorstore import QdrantStore
from qdrant_client import QdrantClient


@pytest.fixture
def store() -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)
    store._client = QdrantClient(":memory:")
    store._collection = "test"
    store.ensure_collection()
    return store


def _vector(seed: float) -> list[float]:
    return [seed] * 384


def test_upsert_then_search_returns_payload(store: QdrantStore):
    store.upsert_batch(
        [
            {
                "id": 1,
                "vector": _vector(0.9),
                "payload": {"text": "Python is a language", "title": "Python", "source_id": "a"},
            }
        ]
    )

    results = store.search(_vector(0.9), top_k=1)

    assert len(results) == 1
    assert results[0]["text"] == "Python is a language"
    assert results[0]["title"] == "Python"
    assert results[0]["source_id"] == "a"
    # identical vectors score ~1.0; allow float slack (cosine can return 1.0000001)
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-3)


def test_search_respects_top_k(store: QdrantStore):
    store.upsert_batch(
        [
            {"id": i, "vector": _vector(0.1 * i), "payload": {"text": f"chunk {i}"}}
            for i in range(1, 6)
        ]
    )

    assert len(store.search(_vector(0.3), top_k=2)) == 2


def test_search_on_empty_collection_returns_empty(store: QdrantStore):
    assert store.search(_vector(0.5), top_k=5) == []


def test_count_reflects_upserts(store: QdrantStore):
    assert store.count() == 0
    store.upsert_batch([{"id": 1, "vector": _vector(0.2), "payload": {"text": "x"}}])
    assert store.count() == 1


def test_ensure_collection_rejects_wrong_dim(store: QdrantStore):
    with pytest.raises(ValueError, match="Expected embedding dim"):
        store.ensure_collection(dim=768)
