from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from app.core.citations import build_citations, extract_citation_indices
from app.core.config import settings
from app.core.embeddings import BGEEmbedder, Embedder
from app.core.llm import OllamaLLM
from app.core.metrics import recorder
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


@contextmanager
def _timed(timings: dict[str, float], stage: str):
    """Record a stage's duration even when it raises.

    A Qdrant call that times out after 60 s took 60 s, and that is the sample
    worth having. Timing in a finally block is the only way to keep it.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[stage] = (time.perf_counter() - start) * 1000


class _TimedEmbedder(Embedder):
    """Times `embed` without `retrieve()` knowing that anything is being timed.

    A proxy rather than instrumentation inside retrieval: the retrieval module
    stays about retrieval, and the split between embedding and vector search
    is still measured exactly. Cheap - one perf_counter pair per call.
    """

    def __init__(self, inner: Embedder, timings: dict[str, float]) -> None:
        self._inner = inner
        self._timings = timings

    def embed(self, text: str) -> list[float]:
        with _timed(self._timings, "embed"):
            return self._inner.embed(text)

    def embed_batch(self, texts: list[str], *, batch_size: int | None = None):
        return self._inner.embed_batch(texts, batch_size=batch_size)


class _TimedStore:
    """Times `search`; every other attribute passes straight through."""

    def __init__(self, inner: QdrantStore, timings: dict[str, float]) -> None:
        self._inner = inner
        self._timings = timings

    def search(self, *args, **kwargs):
        with _timed(self._timings, "search"):
            return self._inner.search(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    # Pessimistic default: an exception that escapes this handler is an error,
    # and the sample should say so rather than being silently dropped.
    outcome = "error"

    try:
        # Resolve the cached dependencies BEFORE timing retrieval.
        #
        # These are @lru_cache'd, so the first call after a restart constructs
        # the SentenceTransformer - which loads weights and revalidates them
        # against the Hugging Face Hub. Measured on this machine: 41 s, against
        # ~250 ms for warm retrieval. Resolving them inside the retrieve block
        # (they were arguments to it) charged that one-time cost to `retrieve`
        # and reported a 42-second retrieval that never happened. A metric that
        # misattributes is worse than no metric, so cold start gets its own
        # stage instead of being smeared into a neighbouring one.
        with _timed(timings, "deps"):
            embedder, store = _embedder(), _store()

        try:
            with _timed(timings, "retrieve"):
                chunks, is_empty = retrieve(
                    request.question,
                    _TimedEmbedder(embedder, timings),
                    _TimedStore(store, timings),
                )
        except Exception as exc:
            logger.error("Retrieval error: %s", exc)
            raise HTTPException(status_code=503, detail="Vector store unavailable") from exc

        if is_empty:
            # Hard refusal: retrieval was empty or below the score threshold.
            # Fast, and never calls the LLM - which is exactly why it is kept
            # out of the `ok` latency bucket.
            outcome = "refused"
            return QueryResponse(answer=REFUSAL_MESSAGE, citations=[], refused=True)

        prompt = build_prompt(request.question, chunks)

        # Same reasoning as `deps` above: constructing the client is not
        # generation, so it is not timed as generation.
        llm = _llm()

        try:
            with _timed(timings, "generate"):
                answer = llm.generate(prompt)
        except Exception as exc:
            logger.error("LLM error: %s", exc)
            raise HTTPException(status_code=503, detail="LLM unavailable") from exc

        # Soft refusal: the model itself declined despite having context. Decide
        # by the answer text, never by citation count — a grounded answer may
        # omit [n].
        refused = is_refusal(answer)
        if refused:
            outcome = "refused"
            return QueryResponse(answer=answer, citations=[], refused=True)

        cited_indices = extract_citation_indices(answer)
        citations = build_citations(chunks, cited_indices)

        outcome = "ok"
        return QueryResponse(
            answer=answer,
            citations=[Citation(**citation) for citation in citations],
            refused=False,
        )
    finally:
        total_ms = (time.perf_counter() - started) * 1000
        for stage, ms in timings.items():
            recorder.record(stage, outcome, ms)
        recorder.record("total", outcome, total_ms)
        # One structured line per request, so the timings survive without the
        # /metrics endpoint and can be aggregated by a log pipeline later.
        logger.info(
            "query outcome=%s total_ms=%.1f %s",
            outcome,
            total_ms,
            " ".join(f"{stage}_ms={ms:.1f}" for stage, ms in sorted(timings.items())),
        )
