"""Model registry - a name, mapped to a ready-to-use model.

Deliberately the same shape as `engines/registry.py`, which is itself the shape
`app/core/providers.py` documented, so this codebase has ONE pattern to learn
rather than three. Adding a provider is one line here. Choosing one is one
environment variable:

    LLM_CHOICE   = ollama-3b | ollama-1b | vertex      (default: ollama-3b)
    EMBED_CHOICE = bge | vertex                        (default: bge)

THE DEFAULTS ARE THE SHIPPED BEHAVIOUR. `ollama-3b` and `bge` are exactly what
the served path builds today, so adding this registry changes nothing about
what runs until someone sets one of those values. That is the property
`tests/test_providers.py` pins.

FACTORIES ARE LAZY, TWICE OVER. They are lambdas, so no credential is read and
no client is constructed until a preset is actually selected; and the Vertex
factory imports its module inside the function, so `import providers` works
wherever the `gcp` extra is not installed - every CI job, and any deployment
serving the local models.

WHY `app/` DOES NOT IMPORT THIS. The dependency runs one way: this package
implements interfaces that `app/` declares, and `app/` never reaches back -
the same direction `engines/` follows, asserted in `tests/test_layering.py`.
Wiring `/query` to dispatch through here therefore needs a composition root
outside `app/`, not an import inside it. That is a separate change, and until
it happens the honest description of this registry is that the eval harness and
any future composition root can select a provider, and the served path still
constructs its models directly.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.core.embeddings import BGEEmbedder, Embedder
from app.core.llm import LLM, OllamaLLM

LLMFactory = Callable[[], LLM]
EmbedderFactory = Callable[[], Embedder]

DEFAULT_LLM = "ollama-3b"
DEFAULT_EMBEDDER = "bge"

_OLLAMA_URL = "http://localhost:11434"


def _vertex_llm() -> LLM:
    """Build the Vertex generator, importing its SDK only when it is chosen.

    See the module docstring: a module-level import would make
    google-cloud-aiplatform a hard dependency of `providers`, and would fail at
    import time rather than at the point someone selects the provider.
    """
    from providers.vertex import DEFAULT_LLM_MODEL, DEFAULT_LOCATION, VertexLLM

    return VertexLLM(
        model=os.getenv("VERTEX_LLM_MODEL", DEFAULT_LLM_MODEL),
        # None is correct when unset: Vertex resolves the project from
        # Application Default Credentials, so requiring it here would reject a
        # correctly authenticated environment.
        project=os.getenv("GCP_PROJECT"),
        location=os.getenv("GCP_LOCATION", DEFAULT_LOCATION),
    )


def _vertex_embedder() -> Embedder:
    from providers.vertex import DEFAULT_EMBED_MODEL, DEFAULT_LOCATION, VertexEmbedder

    return VertexEmbedder(
        model=os.getenv("VERTEX_EMBED_MODEL", DEFAULT_EMBED_MODEL),
        project=os.getenv("GCP_PROJECT"),
        location=os.getenv("GCP_LOCATION", DEFAULT_LOCATION),
    )


LLM_REGISTRY: dict[str, LLMFactory] = {
    "ollama-3b": lambda: OllamaLLM("llama3.2:3b", os.getenv("OLLAMA_URL", _OLLAMA_URL)),
    "ollama-1b": lambda: OllamaLLM("llama3.2:1b", os.getenv("OLLAMA_URL", _OLLAMA_URL)),
    "vertex": _vertex_llm,
    # <- ADD A NEW LLM PROVIDER AS ONE LINE HERE
}

EMBED_REGISTRY: dict[str, EmbedderFactory] = {
    "bge": lambda: BGEEmbedder(os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")),
    "vertex": _vertex_embedder,
    # <- ADD A NEW EMBEDDING PROVIDER AS ONE LINE HERE
}


def make_llm(choice: str | None = None) -> LLM:
    """Build the LLM named by *choice*, else by LLM_CHOICE, else the default."""
    name = choice or os.getenv("LLM_CHOICE", DEFAULT_LLM)
    if name not in LLM_REGISTRY:
        raise ValueError(f"Unknown LLM_CHOICE {name!r}; options: {sorted(LLM_REGISTRY)}")
    return LLM_REGISTRY[name]()


def make_embedder(choice: str | None = None) -> Embedder:
    """Build the Embedder named by *choice*, else by EMBED_CHOICE, else the default.

    CHANGING THIS CHANGES THE VECTOR DIMENSION. bge-small is 384-d and
    text-embedding-005 is 768-d, so a switch here requires a re-ingest into a
    NEW collection. `ensure_collection(dim)` compares the embedder's probed
    dimension against the size the existing collection was created at and
    raises naming both, so the mismatch stops at startup instead of surfacing
    as a rejected upsert mid-ingest.
    """
    name = choice or os.getenv("EMBED_CHOICE", DEFAULT_EMBEDDER)
    if name not in EMBED_REGISTRY:
        raise ValueError(f"Unknown EMBED_CHOICE {name!r}; options: {sorted(EMBED_REGISTRY)}")
    return EMBED_REGISTRY[name]()
