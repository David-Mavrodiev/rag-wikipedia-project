"""The v1 chain, expressed as an engine. No framework, and none needed.

This is the baseline every other engine is measured against, so it must be the
SAME pipeline the API has always served - not a reimplementation that happens
to look similar. tests/test_engine_conformance.py asserts exactly that, by
running this and `POST /query` over identical mocks and comparing what comes
back.

It is a first-class registry entry rather than a special case. A registry whose
only real member is the new thing, with the old path bolted on beside it, does
not demonstrate that the choice is real.
"""

from __future__ import annotations

from app.core.citations import build_citations, extract_citation_indices
from app.core.embeddings import Embedder
from app.core.llm import LLM
from app.core.prompt import build_prompt
from app.core.refusal import REFUSAL_MESSAGE, is_refusal
from app.core.retrieval import retrieve
from app.core.vectorstore import QdrantStore

from engines.base import Engine, EngineResult


class DirectEngine(Engine):
    """retrieve -> refuse or generate -> cite. One pass, at most one LLM call."""

    def __init__(self, embedder: Embedder, store: QdrantStore, llm: LLM) -> None:
        self._embedder = embedder
        self._store = store
        self._llm = llm

    def answer(self, question: str) -> EngineResult:
        chunks, is_empty = retrieve(question, self._embedder, self._store)

        if is_empty:
            # HARD refusal: retrieval was empty, below the score floor, or the
            # evidence gate rejected it. This never reaches the model, which is
            # why it costs nothing and why llm_calls stays at zero.
            #
            # The reason is coarse because retrieve() returns a bool and keeps
            # EvidenceDecision.reason to itself. Re-deriving it by calling
            # decide_evidence() on the chunks returned here would be WORSE than
            # coarse: those chunks are token-budget-trimmed, so the answer would
            # describe a different input than the decision was made on. Exposing
            # the real reason means changing retrieve(), which is a watched file
            # and therefore a separate, re-measured change.
            return EngineResult(
                answer=REFUSAL_MESSAGE,
                refused=True,
                refusal_reason="no_evidence",
                stats={"llm_calls": 0, "retrieved": len(chunks)},
            )

        answer = self._llm.generate(build_prompt(question, chunks))

        # SOFT refusal: the model declined despite having context. Decided by
        # the answer TEXT, never by citation count - a grounded answer may
        # legitimately carry no [n] markers at all.
        if is_refusal(answer):
            return EngineResult(
                answer=answer,
                refused=True,
                refusal_reason="soft_refusal",
                stats={"llm_calls": 1, "retrieved": len(chunks)},
            )

        citations = build_citations(chunks, extract_citation_indices(answer))
        return EngineResult(
            answer=answer,
            citations=citations,
            refused=False,
            stats={"llm_calls": 1, "retrieved": len(chunks)},
        )
