from __future__ import annotations

import logging

import tiktoken
from opentelemetry import trace

from app.core.config import settings
from app.core.embeddings import Embedder
from app.core.refusal import decide_evidence, evidence_overlap, is_private_or_time_dependent
from app.core.runtime_config import get_runtime_config
from app.core.tracing import record_question
from app.core.vectorstore import QdrantStore

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

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

    with tracer.start_as_current_span("rag.retrieve") as span:
        span.set_attribute("rag.top_k", top_k)
        span.set_attribute("rag.token_budget", token_budget)
        record_question(span, query)

        if is_private_or_time_dependent(query):
            span.set_attribute("rag.refused", True)
            span.set_attribute("rag.refusal_reason", "private_or_time_dependent")
            return [], True

        # Pure in-process CPU with no socket to it, so no amount of transport
        # auto-instrumentation would ever show this time. On a cold process it
        # also carries the model load.
        with tracer.start_as_current_span("rag.embed") as embed_span:
            embed_span.set_attribute("rag.embed_model", settings.embed_model)
            embed_span.set_attribute("rag.query_tokens", _token_count(query))
            vector = embedder.embed(query)

        candidate_k = max(top_k, get_runtime_config().retrieval_candidate_k)

        # Wraps the auto-instrumented HTTP span rather than replacing it: the
        # transport span disappears if Qdrant is reached over gRPC or the httpx
        # instrumentation is absent, and the timing of this stage should not.
        with tracer.start_as_current_span("rag.search") as search_span:
            search_span.set_attribute("rag.collection", settings.collection)
            search_span.set_attribute("rag.candidate_k", candidate_k)
            candidates = store.search(vector, top_k=candidate_k)
            search_span.set_attribute("rag.candidates_returned", len(candidates))

        with tracer.start_as_current_span("rag.rerank"):
            results = _rerank(query, candidates)[:top_k]

        evidence = decide_evidence(query, results)
        span.set_attribute("rag.top_score", evidence.top_score)
        span.set_attribute("rag.score_margin", evidence.score_margin)
        span.set_attribute("rag.overlap_terms", len(evidence.overlap_terms))
        if evidence.refused:
            span.set_attribute("rag.refused", True)
            span.set_attribute("rag.refusal_reason", evidence.reason)
            return results, True

        with tracer.start_as_current_span("rag.token_budget") as budget_span:
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

            budget_span.set_attribute("rag.tokens_used", used)
            budget_span.set_attribute("rag.chunks_dropped", len(results) - len(kept))

        span.set_attribute("rag.chunks_returned", len(kept))
        span.set_attribute("rag.refused", len(kept) == 0)
        return kept, len(kept) == 0
