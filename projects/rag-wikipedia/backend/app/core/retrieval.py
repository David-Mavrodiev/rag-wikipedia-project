from __future__ import annotations

import logging

import tiktoken

from app.core.config import settings
from app.core.embeddings import Embedder
from app.core.vectorstore import QdrantStore

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    return len(_enc.encode(text))


def retrieve(
    query: str,
    embedder: Embedder,
    store: QdrantStore,
    top_k: int | None = None,
    token_budget: int | None = None,
) -> tuple[list[dict], bool]:
    if top_k is None:
        top_k = settings.top_k
    if token_budget is None:
        token_budget = settings.token_budget

    vector = embedder.embed(query)
    results = store.search(vector, top_k=top_k)

    if not results:
        return [], True

    if results[0]["score"] < settings.refusal_threshold:
        return results, True

    kept: list[dict] = []
    used = 0
    for index, result in enumerate(results):
        tokens = _token_count(result["text"])
        if index == 0:
            kept.append(result)
            used = tokens
            continue
        if used + tokens > token_budget:
            break
        kept.append(result)
        used += tokens

    return kept, len(kept) == 0
