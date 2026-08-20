"""Guards on the ingestion benchmark's throwaway collection.

The benchmark DROPS its collection when it finishes, so pointing it at real data
destroys the corpus. These tests lock the safety rules that prevent that.
"""

import pytest
from app.core.config import settings
from app.core.vectorstore import QdrantStore
from eval.bench_ingest import DEFAULT_BENCH_PREFIX, claim_collection, resolve_collection
from qdrant_client import QdrantClient


def test_unset_gives_a_unique_throwaway_name(monkeypatch):
    monkeypatch.delenv("BENCH_COLLECTION", raising=False)
    first = resolve_collection()
    second = resolve_collection()
    assert first.startswith(f"{DEFAULT_BENCH_PREFIX}_")
    assert first != second  # unique per run -> nothing pre-existing to destroy
    assert first != settings.collection


def test_refuses_the_primary_collection(monkeypatch):
    # THE regression: BENCH_COLLECTION=wikipedia used to delete the real corpus.
    monkeypatch.setenv("BENCH_COLLECTION", settings.collection)
    with pytest.raises(SystemExit):
        resolve_collection()


def test_explicit_name_is_honoured(monkeypatch):
    monkeypatch.setenv("BENCH_COLLECTION", "bench_custom")
    assert resolve_collection() == "bench_custom"


# --- ownership: creation is the proof, never a check-then-create race --------


def _memory_store(collection: str) -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)
    store._client = QdrantClient(":memory:")
    store._collection = collection
    return store


def test_claim_succeeds_on_a_free_name():
    store = _memory_store("bench_free")
    claim_collection(store, "bench_free")
    assert store._client.collection_exists("bench_free")


def test_claim_refuses_a_name_that_already_exists():
    # THE regression: the benchmark deletes its collection when it finishes, so
    # claiming a name someone else created would destroy their data.
    store = _memory_store("bench_taken")
    claim_collection(store, "bench_taken")          # first run owns it

    with pytest.raises(SystemExit):
        claim_collection(store, "bench_taken")      # second must refuse


def test_claim_reraises_a_genuine_failure():
    # A failure that is NOT "already exists" must surface, not be swallowed as
    # a polite refusal (e.g. server unreachable, bad vector config).
    class Broken:
        def create_collection(self, **kwargs):
            raise RuntimeError("connection refused")

        def collection_exists(self, name):
            return False

    store = QdrantStore.__new__(QdrantStore)
    store._client = Broken()
    store._collection = "bench_x"

    with pytest.raises(RuntimeError, match="connection refused"):
        claim_collection(store, "bench_x")
