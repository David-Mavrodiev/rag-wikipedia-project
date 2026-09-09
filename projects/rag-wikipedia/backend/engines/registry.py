"""Engine registry - a name, mapped to a ready-to-use engine.

Deliberately the same shape as the LLM and embedder registry in
`app/core/providers.py`, so this codebase has one pattern to learn rather than
three. Adding an engine is one line here. Choosing one is one environment
variable.

    ENGINE_CHOICE=direct     the v1 chain (the default)

ENGINE_CHOICE is read from the environment rather than declared on `Settings`
for now. Settings would be better - it validates at startup - but `config.py`
is watched by audit_freshness, so a field there costs a re-measurement of a
corpus this value cannot affect. It earns its place in Settings when `/query`
actually dispatches through the registry and the value starts gating served
behaviour. Until then an unknown name still fails loudly, at construction, in
make_engine.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.core.embeddings import Embedder
from app.core.llm import LLM
from app.core.vectorstore import QdrantStore

from engines.base import Engine
from engines.direct import DirectEngine

EngineFactory = Callable[[Embedder, QdrantStore, LLM], Engine]

DEFAULT_ENGINE = "direct"

ENGINE_REGISTRY: dict[str, EngineFactory] = {
    "direct": lambda embedder, store, llm: DirectEngine(embedder, store, llm),
    # <- ADD A NEW ENGINE AS ONE LINE HERE
}


def make_engine(
    embedder: Embedder,
    store: QdrantStore,
    llm: LLM,
    choice: str | None = None,
) -> Engine:
    """Build the engine named by *choice*, else by ENGINE_CHOICE, else default.

    Factories take their dependencies rather than reading them from settings,
    so the eval harness and the API can hand an engine exactly the embedder,
    store and model they intend to measure - instrumented wrappers included.
    """
    name = choice or os.getenv("ENGINE_CHOICE", DEFAULT_ENGINE)
    if name not in ENGINE_REGISTRY:
        raise ValueError(f"Unknown engine {name!r}; options: {sorted(ENGINE_REGISTRY)}")
    return ENGINE_REGISTRY[name](embedder, store, llm)
