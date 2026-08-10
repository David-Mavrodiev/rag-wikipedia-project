from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException

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
router = APIRouter()


@lru_cache(maxsize=1)
def _embedder() -> BGEEmbedder:
    return BGEEmbedder(model_name=settings.embed_model)


@lru_cache(maxsize=1)
def _store() -> QdrantStore:
    return QdrantStore(url=settings.qdrant_url, collection=settings.collection)


@lru_cache(maxsize=1)
def _llm() -> OllamaLLM:
    return OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    try:
        chunks, is_empty = retrieve(request.question, _embedder(), _store())
    except Exception as exc:
        logger.error("Retrieval error: %s", exc)
        raise HTTPException(status_code=503, detail="Vector store unavailable") from exc

    if is_empty:
        # Hard refusal: retrieval was empty or below the score threshold.
        return QueryResponse(answer=REFUSAL_MESSAGE, citations=[], refused=True)

    prompt = build_prompt(request.question, chunks)

    try:
        answer = _llm().generate(prompt)
    except Exception as exc:
        logger.error("LLM error: %s", exc)
        raise HTTPException(status_code=503, detail="LLM unavailable") from exc

    # Soft refusal: the model itself declined despite having context. Decide by
    # the answer text, never by citation count — a grounded answer may omit [n].
    refused = is_refusal(answer)
    if refused:
        return QueryResponse(answer=answer, citations=[], refused=True)

    cited_indices = extract_citation_indices(answer)
    citations = build_citations(chunks, cited_indices)

    return QueryResponse(
        answer=answer,
        citations=[Citation(**citation) for citation in citations],
        refused=False,
    )
