"""The store registry: a second store without touching the first.

The point of these is the claim the design rests on - `QdrantStore` satisfies
the Protocol UNMODIFIED, so adding Azure AI Search costs no edit to a file that
`audit_freshness` watches, and no re-measurement of a corpus a type hint cannot
affect.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.stores import VectorStore, WritableVectorStore
from providers.stores import DEFAULT_STORE, STORE_REGISTRY, make_store


class _Store:
    """Everything a serving path needs, and nothing it does not."""

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        return [{"score": 0.9, "text": "t", "title": "T", "source_id": "1"}]

    def count(self) -> int:
        return 1


class _Partial:
    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        return []


# --- the Protocol -------------------------------------------------------------

def test_the_real_store_satisfies_the_protocol_without_being_modified():
    # The whole design rests on this: structural typing, so vectorstore.py -
    # which audit_freshness watches - stays out of the diff.
    from app.core.vectorstore import QdrantStore

    assert isinstance(QdrantStore, type)
    for method in ("search", "count", "ensure_collection", "upsert_batch", "existing_ids"):
        assert callable(getattr(QdrantStore, method, None)), method


def test_a_store_that_can_only_read_is_still_a_store():
    assert isinstance(_Store(), VectorStore)
    assert not isinstance(_Store(), WritableVectorStore)


def test_something_missing_count_is_not_a_store():
    assert not isinstance(_Partial(), VectorStore)


# --- the registry -------------------------------------------------------------

def test_qdrant_is_the_default_so_nothing_moves_by_upgrading():
    assert DEFAULT_STORE == "qdrant"
    assert "qdrant" in STORE_REGISTRY


def test_an_unknown_store_fails_loudly_at_construction():
    with pytest.raises(ValueError, match="Unknown store"):
        make_store("sqlite-with-hope")


def test_the_choice_comes_from_the_environment_when_not_given(monkeypatch):
    built = {}
    monkeypatch.setitem(STORE_REGISTRY, "fake", lambda **kw: built.update(kw) or _Store())
    monkeypatch.setenv("STORE_CHOICE", "fake")

    store = make_store(url="http://q", collection="wikipedia")

    assert isinstance(store, VectorStore)
    assert built == {"url": "http://q", "collection": "wikipedia"}


def test_an_explicit_choice_beats_the_environment(monkeypatch):
    monkeypatch.setitem(STORE_REGISTRY, "fake", lambda **kw: _Store())
    monkeypatch.setenv("STORE_CHOICE", "qdrant")

    assert isinstance(make_store("fake"), _Store)


def test_no_store_imports_its_client_at_module_level():
    # Same rule as providers/vertex.py: `import app.core.stores` must work on a
    # machine that installed none of the optional extras, which includes CI.
    # Every factory therefore imports inside itself.
    import inspect

    for name, factory in STORE_REGISTRY.items():
        body = inspect.getsource(factory)
        assert "    from " in body or "    import " in body, f"{name} imports too early"
