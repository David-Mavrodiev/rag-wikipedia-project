"""Contract tests against the real qdrant-client API surface.

These use an in-memory Qdrant (":memory:") rather than mocks: mocking the client
is what let the removal of QdrantClient.search() reach production unnoticed.
"""

from __future__ import annotations

import pytest
from app.core.vectorstore import QdrantStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams


@pytest.fixture
def store() -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)
    store._client = QdrantClient(":memory:")
    store._collection = "test"
    store.ensure_collection(dim=384)
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


def _memory_store(collection: str) -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)
    store._client = QdrantClient(":memory:")
    store._collection = collection
    return store


def test_ensure_collection_rejects_a_dim_the_collection_disagrees_with(store: QdrantStore):
    # The fixture created "test" at 384. A 768-d embedder - Vertex
    # text-embedding-005, or bge-base - must be refused HERE, naming both
    # numbers, rather than later as a rejected upsert far from its cause.
    with pytest.raises(ValueError, match="stores 384-d vectors but the embedder produces 768-d"):
        store.ensure_collection(dim=768)


def test_ensure_collection_is_idempotent_at_a_matching_dim(store: QdrantStore):
    # Re-running an ingest against its own collection is the normal case, and
    # ingestion is deliberately resumable - this must not raise.
    store.ensure_collection(dim=384)
    assert store.count() == 0


def test_ensure_collection_creates_at_a_dim_other_than_384():
    # The point of the change: 768 is a dimension, not an error. Before this,
    # the store rejected every embedding model except bge-small.
    store = _memory_store("vertex_sized")
    store.ensure_collection(dim=768)

    stored = store._client.get_collection("vertex_sized").config.params.vectors
    assert stored.size == 768


def test_ensure_collection_refuses_a_named_vector_collection():
    # Not a shape this store creates. Picking one of the named sizes and
    # hoping is how you write 768-d vectors into someone else's 384-d field.
    store = _memory_store("named")
    store._client.create_collection(
        collection_name="named",
        vectors_config={"text": VectorParams(size=384, distance=Distance.COSINE)},
    )

    with pytest.raises(ValueError, match="named-vector configuration"):
        store.ensure_collection(dim=384)
