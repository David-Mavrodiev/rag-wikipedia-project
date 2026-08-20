from __future__ import annotations

import logging

import tiktoken

from app.core.config import settings
from app.core.embeddings import Embedder
from app.core.refusal import decide_evidence, evidence_overlap, is_private_or_time_dependent
from app.core.runtime_config import get_runtime_config
from app.core.vectorstore import QdrantStore

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    return len(_enc.encode(text))


def _rerank(query: str, results: list[dict]) -> list[dict]:
    def score(result: dict) -> tuple[float, float]:
        overlap_count = len(evidence_overlap(query, [result]))
        vector_score = float(result.get("score", 0.0))
        return (overlap_count, vector_score)

    return sorted(results, key=score, reverse=True)


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

    if is_private_or_time_dependent(query):
        return [], True

    vector = embedder.embed(query)
    candidate_k = max(top_k, get_runtime_config().retrieval_candidate_k)
    results = _rerank(query, store.search(vector, top_k=candidate_k))[:top_k]

    evidence = decide_evidence(query, results)
    if evidence.refused:
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
