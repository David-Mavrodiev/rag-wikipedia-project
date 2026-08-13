"""Guards on the ingestion benchmark's throwaway collection.

The benchmark DROPS its collection when it finishes, so pointing it at real data
destroys the corpus. These tests lock the safety rules that prevent that.
"""

import pytest
from app.core.config import settings
from eval.bench_ingest import DEFAULT_BENCH_PREFIX, resolve_collection


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
