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
    embedder.embed_batch.side_effect = lambda texts, **kw: [[0.1] * 384 for _ in texts]
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


# --- resumability: the expensive half must be skippable ----------------------

def _resumable_store():
    """A store whose existing_ids reflects what has actually been upserted."""
    store = _make_store()
    store.existing_ids.side_effect = lambda ids: {i for i in ids if i in store._storage}
    return store


def _run_flow(store, embedder, monkeypatch, *, force=False, articles=None):
    """Drive ingest_flow with the network, models and Prefect runtime replaced.

    The tasks are swapped for their plain `.fn` bodies. Calling them as Prefect
    tasks outside a flow context starts a temporary Prefect server per test,
    which cost ~22s and a stream of teardown warnings for no extra coverage -
    the orchestration layer is not what these tests are about.
    """
    from pipeline import flow as flow_mod
    from pipeline import tasks as tasks_mod

    for name in ("clean_article", "chunk_article", "embed_chunks", "upsert_to_qdrant",
                 "ingest_segment"):
        task = getattr(tasks_mod, name)
        # idempotent: a test may call this helper twice, and the second
        # time the attribute is already the unwrapped function.
        monkeypatch.setattr(tasks_mod, name, getattr(task, "fn", task))

    monkeypatch.setattr(
        "pipeline.sources.iter_articles", lambda profile: iter(articles or FIXTURE_ARTICLES)
    )
    # the flow batches, so shrink the segment to exercise flushing with the
    # two-article fixture rather than only the tail flush
    monkeypatch.setattr("pipeline.flow.SEGMENT_ARTICLES", 1)
    monkeypatch.setattr("app.core.embeddings.BGEEmbedder", lambda model_name: embedder)
    monkeypatch.setattr("app.core.vectorstore.QdrantStore", lambda url, collection: store)
    return flow_mod.ingest_flow.fn("tiny", force=force)


def test_second_run_skips_embedding_entirely(monkeypatch):
    # The point of resumability: a re-run must not pay the embedding cost again.
    # Deterministic IDs already made the upsert idempotent; embedding is ~97% of
    # ingestion time and was repeated in full on every restart.
    store = _resumable_store()
    embedder = _make_embedder()

    first = _run_flow(store, embedder, monkeypatch)
    calls_after_first = embedder.embed_batch.call_count
    count_after_first = len(store._storage)

    second = _run_flow(store, embedder, monkeypatch)

    assert first > 0
    assert second == 0, "second run upserted chunks it already had"
    assert embedder.embed_batch.call_count == calls_after_first, "re-embedded on resume"
    assert len(store._storage) == count_after_first, "vector count changed on re-run"


def test_partial_article_embeds_only_the_missing_chunks(monkeypatch):
    # An interrupted run leaves an article half-stored. Only the gap should be
    # re-embedded, not the whole article.
    store = _resumable_store()
    embedder = _make_embedder()
    _run_flow(store, embedder, monkeypatch)

    stored_ids = list(store._storage)
    dropped = stored_ids[:3]
    for point_id in dropped:
        del store._storage[point_id]

    embedder.embed_batch.reset_mock()
    _run_flow(store, embedder, monkeypatch)

    embedded = sum(len(call.args[0]) for call in embedder.embed_batch.call_args_list)
    assert embedded == len(dropped), f"re-embedded {embedded} chunks, expected {len(dropped)}"
    assert len(store._storage) == len(stored_ids), "did not restore the dropped chunks"


def test_force_reembeds_everything(monkeypatch):
    # The escape hatch: when the embedding model or chunk size changes the point
    # IDs stay the same while the vectors they should hold do not, so skipping
    # would silently keep stale vectors.
    store = _resumable_store()
    embedder = _make_embedder()
    _run_flow(store, embedder, monkeypatch)

    embedder.embed_batch.reset_mock()
    inserted = _run_flow(store, embedder, monkeypatch, force=True)

    assert inserted > 0, "force did not re-upsert"
    assert embedder.embed_batch.call_count > 0, "force did not re-embed"


def test_fresh_collection_embeds_every_chunk(monkeypatch):
    store = _resumable_store()
    embedder = _make_embedder()

    inserted = _run_flow(store, embedder, monkeypatch)

    embedded = sum(len(call.args[0]) for call in embedder.embed_batch.call_args_list)
    assert embedded == inserted == len(store._storage)
