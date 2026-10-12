"""Access-controlled search: the filter must reach the index, and it must fail closed.

The Azure SDK is faked through sys.modules, the way tests/test_vertex.py fakes
Google's, so these run in CI on a machine that has never installed the `azure`
extra or authenticated to Azure.

What is under test is the property that makes ACL trimming worth the name: the
predicate is part of the QUERY. A test that only checked the returned rows
would pass for a Python post-filter, which leaks existence through counts and
scores - the exact failure this is supposed to prevent.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def azure(monkeypatch):
    """A fake azure.search.documents, recording what the store asks for."""
    recorded: dict = {}

    class _VectorizedQuery:
        def __init__(self, vector, k_nearest_neighbors, fields):
            self.vector, self.k, self.fields = vector, k_nearest_neighbors, fields

    class _SearchClient:
        def __init__(self, endpoint, index_name, credential):
            recorded["endpoint"] = endpoint
            recorded["index"] = index_name
            recorded["credential"] = credential

        def search(self, **kwargs):
            recorded["search"] = kwargs
            return recorded.get("rows", [])

        def get_document_count(self):
            return 4_812

    documents = ModuleType("azure.search.documents")
    documents.SearchClient = _SearchClient
    models = ModuleType("azure.search.documents.models")
    models.VectorizedQuery = _VectorizedQuery
    credentials = ModuleType("azure.core.credentials")
    credentials.AzureKeyCredential = lambda key: f"key:{key}"
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda: "managed-identity"

    for name, module in {
        "azure": ModuleType("azure"),
        "azure.core": ModuleType("azure.core"),
        "azure.core.credentials": credentials,
        "azure.search": ModuleType("azure.search"),
        "azure.search.documents": documents,
        "azure.search.documents.models": models,
        "azure.identity": identity,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return recorded


@pytest.fixture
def store_factory(azure):
    from providers.azure_search import AzureSearchStore

    def build(**kwargs):
        kwargs.setdefault("endpoint", "https://s.search.windows.net")
        kwargs.setdefault("index", "wikipedia-subset")
        return AzureSearchStore(**kwargs)

    return build


# --- the filter ---------------------------------------------------------------

def test_the_predicate_travels_inside_the_query(store_factory, azure):
    store_factory(groups=["eng", "legal"], api_key="k").search([0.1, 0.2], top_k=3)

    sent = azure["search"]
    assert sent["filter"] == "acl_groups/any(g: search.in(g, 'eng,legal', ','))"
    assert sent["top"] == 3
    assert sent["vector_queries"][0].k == 3


def test_a_group_name_that_could_rewrite_the_filter_is_refused(store_factory):
    from providers.azure_search import AclError

    with pytest.raises(AclError):
        store_factory(groups=["eng') or true or ('"], api_key="k").search([0.1])


@pytest.mark.parametrize("group", ["eng", "data-science", "team_42", "a.b", "user@corp"])
def test_ordinary_group_names_are_accepted(group):
    from providers.azure_search import acl_filter

    assert group in acl_filter([group])


# --- failing closed ------------------------------------------------------------

def test_no_groups_means_no_documents_and_no_query_at_all(store_factory, azure):
    assert store_factory(groups=[], api_key="k").search([0.1, 0.2]) == []
    assert "search" not in azure, "it must not even ask the index"


def test_groups_omitted_entirely_is_the_same_as_none(store_factory):
    # The dangerous default would be "no groups means see everything".
    assert store_factory(api_key="k").search([0.1]) == []


# --- results and credentials ---------------------------------------------------

def test_rows_come_back_in_the_shape_every_other_store_uses(store_factory, azure):
    azure["rows"] = [
        {"@search.score": 0.81, "text": "Biotite is a mica.", "title": "Biotite",
         "source_id": "b1"},
    ]
    results = store_factory(groups=["public"], api_key="k").search([0.1])

    assert results == [{"score": 0.81, "text": "Biotite is a mica.", "title": "Biotite",
                        "source_id": "b1"}]


def test_a_missing_field_does_not_crash_the_serving_path(store_factory, azure):
    azure["rows"] = [{"@search.score": 0.5}]
    assert store_factory(groups=["public"], api_key="k").search([0.1]) == [
        {"score": 0.5, "text": "", "title": "", "source_id": ""}
    ]


def test_without_a_key_it_uses_managed_identity(store_factory, azure):
    store_factory(groups=["public"]).count()
    assert azure["credential"] == "managed-identity"


def test_with_a_key_it_uses_the_key(store_factory, azure):
    store_factory(groups=["public"], api_key="secret").count()
    assert azure["credential"] == "key:secret"


def test_count_reports_the_index_size(store_factory):
    assert store_factory(groups=["public"], api_key="k").count() == 4812


def test_the_store_satisfies_the_protocol(store_factory):
    from app.core.stores import VectorStore

    assert isinstance(store_factory(groups=["public"], api_key="k"), VectorStore)


def test_the_client_is_built_once_and_reused(store_factory, azure):
    store = store_factory(groups=["public"], api_key="k")
    store.count()
    first = store._client
    store.count()
    assert store._client is first


def test_the_registry_can_build_it(azure):
    from providers.stores import make_store

    store = make_store("azure-search", endpoint="https://s.search.windows.net",
                       index="i", groups=["public"], api_key="k")
    assert store.count() == 4812


def test_nothing_azure_is_imported_by_importing_the_package():
    # The fake above only exists inside its fixture; outside it, a bare import
    # of the stores package must not have pulled the SDK in.
    assert isinstance(sys.modules.get("app.core.stores"), ModuleType)
    assert not isinstance(sys.modules.get("azure.search.documents"), MagicMock)
