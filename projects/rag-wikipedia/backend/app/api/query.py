from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from opentelemetry import trace

from app.core.citations import build_citations, extract_citation_indices
from app.core.config import settings
from app.core.embeddings import BGEEmbedder
from app.core.llm import OllamaLLM
from app.core.prompt import build_prompt
from app.core.refusal import REFUSAL_MESSAGE, is_refusal
from app.core.retrieval import retrieve
from app.core.vectorstore import QdrantStore
from app.models.query import Citation, QueryRequest, QueryResponse

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
router = APIRouter()


# The bodies below run on a cache MISS only, so these spans appear on the first
# request after every start, restart and scale-out - and on no other request.
# Loading a sentence-transformers model takes seconds, and without a span of its
# own that request is an unexplained outlier that drags p99 and gets
# investigated again every time someone notices it.
@lru_cache(maxsize=1)
def _embedder() -> BGEEmbedder:
    with tracer.start_as_current_span("rag.cold_start") as span:
        span.set_attribute("rag.component", "embedder")
        return BGEEmbedder(model_name=settings.embed_model)


@lru_cache(maxsize=1)
def _store() -> QdrantStore:
    with tracer.start_as_current_span("rag.cold_start") as span:
        span.set_attribute("rag.component", "store")
        return QdrantStore(url=settings.qdrant_url, collection=settings.collection)


@lru_cache(maxsize=1)
def _llm() -> OllamaLLM:
    with tracer.start_as_current_span("rag.cold_start") as span:
        span.set_attribute("rag.component", "llm")
        return OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)


def _record_refusal(span: trace.Span, kind: str) -> None:
    """Both refusal paths answer 200, so status codes say nothing about them.

    These two attributes are the only thing that separates "answering fine" from
    "refusing every question" in a dashboard.
    """
    span.set_attribute("rag.refused", True)
    span.set_attribute("rag.refusal_kind", kind)


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    # The server span, made current by the ASGI instrumentation. Outcome
    # attributes go here so they are queryable without joining to a child.
    span = trace.get_current_span()

    try:
        chunks, is_empty = retrieve(request.question, _embedder(), _store())
    except Exception as exc:
        # exception(), not error(): the stack is the only part of this that says
        # WHICH call failed, and it was being thrown away.
        logger.exception("Retrieval error")
        raise HTTPException(status_code=503, detail="Vector store unavailable") from exc

    if is_empty:
        # Hard refusal: retrieval was empty or below the score threshold.
        _record_refusal(span, "hard")
        return QueryResponse(answer=REFUSAL_MESSAGE, citations=[], refused=True)

    prompt = build_prompt(request.question, chunks)

    try:
        # The try/except is OUTSIDE the span so the original exception is what
        # gets recorded on it; converting to HTTPException first would record
        # the translation instead of the cause.
        with tracer.start_as_current_span("rag.generate") as generate_span:
            generate_span.set_attribute("gen_ai.system", "ollama")
            generate_span.set_attribute("gen_ai.request.model", settings.llm_model)
            generate_span.set_attribute("rag.context_chunks", len(chunks))
            answer = _llm().generate(prompt)
    except Exception as exc:
        logger.exception("LLM error")
        raise HTTPException(status_code=503, detail="LLM unavailable") from exc

    # Soft refusal: the model itself declined despite having context. Decide by
    # the answer text, never by citation count — a grounded answer may omit [n].
    refused = is_refusal(answer)
    if refused:
        _record_refusal(span, "soft")
        return QueryResponse(answer=answer, citations=[], refused=True)

    with tracer.start_as_current_span("rag.citations") as citations_span:
        cited_indices = extract_citation_indices(answer)
        citations = build_citations(chunks, cited_indices)
        citations_span.set_attribute("rag.citations_count", len(citations))

    span.set_attribute("rag.refused", False)
    span.set_attribute("rag.citations_count", len(citations))

    return QueryResponse(
        answer=answer,
        citations=[Citation(**citation) for citation in citations],
        refused=False,
    )
