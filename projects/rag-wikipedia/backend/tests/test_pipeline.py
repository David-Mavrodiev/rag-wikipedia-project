from unittest.mock import MagicMock

FIXTURE_ARTICLES = [
    {"id": "1", "title": "Python programming", "text": "Python is a programming language. " * 50},
    {"id": "2", "title": "Machine learning", "text": "Machine learning is a subset of AI. " * 50},
]


def _make_store():
    store = MagicMock()
    store.upsert_batch = MagicMock()
    store.ensure_collection = MagicMock()
    storage = {}

    def fake_upsert(points):
        for point in points:
            storage[point["id"]] = point

    store.upsert_batch.side_effect = fake_upsert
    store._storage = storage
    return store


def _make_embedder():
    embedder = MagicMock()
    embedder.embed_batch.side_effect = lambda texts: [[0.1] * 384 for _ in texts]
    return embedder


def test_idempotency():
    """Re-running the pipeline on the same articles should not change vector count."""
    from pipeline.tasks import chunk_article, clean_article, embed_chunks, upsert_to_qdrant

    store = _make_store()
    embedder = _make_embedder()

    def run_pipeline():
        for article in FIXTURE_ARTICLES:
            cleaned = clean_article.fn(article)
            chunks = chunk_article.fn(cleaned)
            chunk_vectors = embed_chunks.fn(chunks, embedder)
            upsert_to_qdrant.fn(chunk_vectors, store, cleaned)

    run_pipeline()
    count_after_first = len(store._storage)
    run_pipeline()
    count_after_second = len(store._storage)

    assert count_after_first == count_after_second, "Re-run changed vector count — not idempotent"
    assert count_after_first > 0
