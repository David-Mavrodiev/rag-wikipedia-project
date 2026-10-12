"""Store registry - a name, mapped to a ready-to-use vector store.

The same shape as `engines/registry.py` and `app/core/providers.py`, so this
codebase has one pattern to learn rather than three. Adding a store is one line
here. Choosing one is one environment variable.

    STORE_CHOICE=qdrant         what every published number was measured on
    STORE_CHOICE=azure-search   a managed index, with server-side ACL trimming

WHY THIS LIVES IN `providers/` AND NOT IN `app/`. The same rule the model
registry follows, and tests/test_layering.py enforces: `app/` declares the
VectorStore Protocol, this package names implementations of it. A registry
inside the core would point the core at a package whose job is cloud SDK
adapters. Wiring `/query` to dispatch through here is a composition root at the
edge, not an import in the middle.

STORE_CHOICE is read from the environment rather than declared on `Settings`,
for the reason `ENGINE_CHOICE` gives: `config.py` is watched by
`audit_freshness`, and a field there costs a re-measurement of a corpus this
value cannot affect until it gates served behaviour. An unknown name still
fails loudly, at construction, here.

The factories take their connection details as arguments rather than reading
settings, so the eval harness and the API can point a store at a specific
corpus - including a subset built for a capability demo - without a global.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.core.stores import VectorStore

StoreFactory = Callable[..., VectorStore]

DEFAULT_STORE = "qdrant"


def _qdrant(**kwargs: Any) -> VectorStore:
    """The store every published measurement in this repository was made on."""
    from app.core.vectorstore import QdrantStore

    return QdrantStore(url=kwargs["url"], collection=kwargs["collection"])


def _azure_search(**kwargs: Any) -> VectorStore:
    """Azure AI Search, with the caller's groups bound to the instance.

    The adapter lives in `providers/`, beside the Vertex one, because
    tests/test_layering.py forbids a cloud SDK anywhere under `app/` - lazily
    imported or not, it walks the AST and finds it. `app.core` keeps the
    interface and the registry; the SDK stays outside.
    """
    from providers.azure_search import AzureSearchStore

    return AzureSearchStore(**kwargs)


STORE_REGISTRY: dict[str, StoreFactory] = {
    "qdrant": _qdrant,
    "azure-search": _azure_search,
    # <- ADD A NEW STORE AS ONE LINE HERE. Its SDK is imported inside its
    # factory, never at module level: `import app.core.stores` has to work on a
    # machine that installed none of the optional extras, which includes CI.
}


def make_store(choice: str | None = None, **kwargs: Any) -> VectorStore:
    """Build the store named by *choice*, else by STORE_CHOICE, else the default."""
    name = choice or os.getenv("STORE_CHOICE", DEFAULT_STORE)
    if name not in STORE_REGISTRY:
        raise ValueError(f"Unknown store {name!r}; options: {sorted(STORE_REGISTRY)}")
    return STORE_REGISTRY[name](**kwargs)
