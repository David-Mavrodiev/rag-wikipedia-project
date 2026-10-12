"""Store registry - a name, mapped to a ready-to-use vector store.

The same shape as `engines/registry.py` and `app/core/providers.py`, so this
codebase has one pattern to learn rather than three. Adding a store is one line
here. Choosing one is one environment variable.

    STORE_CHOICE=qdrant         what every published number was measured on

`azure-search` is added here in phase 4, with its adapter.

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

from app.core.stores.base import VectorStore

StoreFactory = Callable[..., VectorStore]

DEFAULT_STORE = "qdrant"


def _qdrant(**kwargs: Any) -> VectorStore:
    """The store every published measurement in this repository was made on."""
    from app.core.vectorstore import QdrantStore

    return QdrantStore(url=kwargs["url"], collection=kwargs["collection"])


STORE_REGISTRY: dict[str, StoreFactory] = {
    "qdrant": _qdrant,
    # <- ADD A NEW STORE AS ONE LINE HERE. Its SDK is imported inside its
    # factory, never at module level: `import app.core.stores` has to work on a
    # machine that installed none of the optional extras, which includes CI.
    # `azure-search` lands here with app/core/stores/azure_search.py (phase 4);
    # a name pointing at a module that does not exist yet would be a landmine.
}


def make_store(choice: str | None = None, **kwargs: Any) -> VectorStore:
    """Build the store named by *choice*, else by STORE_CHOICE, else the default."""
    name = choice or os.getenv("STORE_CHOICE", DEFAULT_STORE)
    if name not in STORE_REGISTRY:
        raise ValueError(f"Unknown store {name!r}; options: {sorted(STORE_REGISTRY)}")
    return STORE_REGISTRY[name](**kwargs)
