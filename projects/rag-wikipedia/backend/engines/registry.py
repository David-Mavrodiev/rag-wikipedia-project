"""Engine registry - a name, mapped to a ready-to-use engine.

Deliberately the same shape as the LLM and embedder registry in
`app/core/providers.py`, so this codebase has one pattern to learn rather than
three. Adding an engine is one line here. Choosing one is one environment
variable.

    ENGINE_CHOICE=direct     the v1 chain (the default)
    ENGINE_CHOICE=langgraph  a graph: grade, rewrite, verify
    ENGINE_CHOICE=langchain  an LCEL chain whose middle step is the model choosing

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
from app.core.stores import VectorStore

from engines.base import Engine
from engines.direct import DirectEngine

EngineFactory = Callable[[Embedder, VectorStore, LLM], Engine]

DEFAULT_ENGINE = "direct"

def _langgraph(embedder: Embedder, store: VectorStore, llm: LLM) -> Engine:
    """Build the graph engine, importing its runtime only when it is chosen.

    LAZY on purpose. A module-level import here would make langgraph a hard
    dependency of `engines`, so `import engines` would fail wherever the agent
    extra is not installed - which includes the eval-quality CI job, and any
    deployment that serves the default `direct` engine and has no reason to ship
    a graph runtime. The same reasoning as the lazy SDK imports in providers.py.
    """
    from engines.langgraph_engine import LangGraphEngine

    return LangGraphEngine(embedder, store, llm)


def _langchain(embedder: Embedder, store: VectorStore, llm: LLM) -> Engine:
    """The LCEL chain with an agent step. Lazy for the same reason as above."""
    from engines.langchain_engine import LangChainEngine

    return LangChainEngine(embedder, store, llm)


ENGINE_REGISTRY: dict[str, EngineFactory] = {
    "direct": lambda embedder, store, llm: DirectEngine(embedder, store, llm),
    "langgraph": _langgraph,
    "langchain": _langchain,
    # <- ADD A NEW ENGINE AS ONE LINE HERE
}


def make_engine(
    embedder: Embedder,
    store: VectorStore,
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
