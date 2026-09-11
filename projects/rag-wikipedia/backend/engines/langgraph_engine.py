"""A LangGraph engine: grade the evidence, rewrite once or twice, verify, answer.

WHY A GRAPH RUNTIME HERE AND NOT IN v1. `direct` is a straight line - retrieve,
generate, cite - and a framework buys nothing for a straight line. This engine
is not a straight line: it decides whether the evidence is good enough, can go
back and ask the question differently, and refuses after a bounded number of
attempts. Conditional routing, with a loop and a budget, is what a graph runtime
is actually for.

WHAT IT IS TRYING TO FIX. The measured defect is false_accept_rate 0.600 on the
holdout suite against a 0.10 gate - the system answering when it should have
declined - and it worsens as the corpus grows, because a larger corpus gives a
weak question more chances to find a plausible-looking chunk. Two levers here:

  * REWRITE gives a question that retrieved badly another attempt before the
    engine gives up. This targets false REFUSALS.
  * VERIFY refuses an answer that cites nothing resolvable. This targets false
    ACCEPTS, which is the number that is actually red.

VERIFY IS A DELIBERATE STRATEGY CHANGE, not a bug fix, and it is the lever being
measured. The served pipeline accepts an uncited answer on purpose, because a
grounded answer may legitimately carry no [n] markers. This engine does not. It
trades false refusals for false accepts, and whether that trade pays is an
empirical question the eval suites answer - including the possibility that it
does not pay at all.

Every step calls `app.core`. Nothing here decides what a refusal IS, what
evidence IS, or how a prompt is built for grounding. Those have one definition
each, they are unit-tested, and both engines share them. What this file owns is
the ORDER, the routing, and the retry budget.
"""

from __future__ import annotations

from typing import TypedDict

from app.core.citations import build_citations, extract_citation_indices
from app.core.embeddings import Embedder
from app.core.llm import LLM
from app.core.prompt import build_prompt
from app.core.refusal import (
    REFUSAL_MESSAGE,
    decide_evidence,
    is_private_or_time_dependent,
    is_refusal,
)
from app.core.retrieval import retrieve
from app.core.vectorstore import QdrantStore
from langgraph.graph import END, START, StateGraph

from engines.base import Engine, EngineResult

# How many times a question may be reworded before the engine gives up. Bounded
# because an unbounded retry loop against a corpus that does not contain the
# answer is an expensive way to arrive at the same refusal.
MAX_REWRITES = 2

# Engine strategy, not the shared grounding contract - which is why it lives
# here rather than in app/core/prompt.py. build_prompt defines how EVERY engine
# grounds an answer; this defines how THIS engine retries, and another engine
# may reasonably retry differently, or not at all.
REWRITE_PROMPT = """\
The following question did not retrieve useful passages from an encyclopedia.

Rewrite it as a single search query more likely to match encyclopedic article
text. Prefer the formal or technical name of the subject. Do not answer the
question, do not explain, and do not add anything that was not asked.

Reply with the rewritten query on one line and nothing else.

Question: {question}
Rewritten query:"""


class _State(TypedDict):
    """What flows between nodes.

    `question` is what was asked and never changes - the final answer and its
    citations belong to it. `query` is what is currently being sent to
    retrieval, and is what a rewrite replaces.
    """

    question: str
    query: str
    chunks: list[dict]
    answer: str
    citations: list[dict]
    refused: bool
    refusal_reason: str | None
    rewrites: int
    llm_calls: int


class LangGraphEngine(Engine):
    def __init__(
        self,
        embedder: Embedder,
        store: QdrantStore,
        llm: LLM,
        *,
        max_rewrites: int = MAX_REWRITES,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._llm = llm
        self._max_rewrites = max_rewrites
        # Compiled once. The graph is immutable and holds no per-question state,
        # so rebuilding it per call would be pure overhead.
        #
        # No checkpointer, deliberately. One earns its place when a run pauses
        # for a human, resumes after a restart, or spans minutes. A synchronous
        # request that either answers or refuses in a single pass has nothing to
        # resume, and adding one here would be machinery with no reader.
        self._graph = self._build().compile()

    # --- nodes -------------------------------------------------------------
    def _triage(self, state: _State) -> dict:
        """Refuse what no corpus can answer, before spending a search on it."""
        if is_private_or_time_dependent(state["question"]):
            return {
                "refused": True,
                "refusal_reason": "private_or_time_dependent_question",
                "answer": REFUSAL_MESSAGE,
            }
        return {}

    def _retrieve(self, state: _State) -> dict:
        chunks, _ = retrieve(state["query"], self._embedder, self._store)
        return {"chunks": chunks}

    def _grade(self, state: _State) -> dict:
        """Ask app.core whether this evidence supports an answer, and record why.

        `decide_evidence` is the same function retrieve() consults internally.
        Calling it here is not a second opinion - it is how this engine SEES the
        decision, because retrieve() returns a bool and keeps the reason.
        """
        decision = decide_evidence(state["query"], state["chunks"])
        return {"refusal_reason": decision.reason, "refused": decision.refused}

    def _rewrite(self, state: _State) -> dict:
        rewritten = self._llm.generate(
            REWRITE_PROMPT.format(question=state["question"])
        ).strip()
        # A model that returns nothing usable must not blank the query and turn
        # a weak search into an empty one. Keep what we had and let the budget
        # run out honestly.
        first_line = rewritten.splitlines()[0].strip() if rewritten else ""
        return {
            "query": first_line or state["query"],
            "rewrites": state["rewrites"] + 1,
            "llm_calls": state["llm_calls"] + 1,
        }

    def _generate(self, state: _State) -> dict:
        answer = self._llm.generate(build_prompt(state["question"], state["chunks"]))
        calls = state["llm_calls"] + 1
        if is_refusal(answer):
            return {
                "answer": answer,
                "refused": True,
                "refusal_reason": "soft_refusal",
                "llm_calls": calls,
            }
        return {"answer": answer, "refused": False, "refusal_reason": None, "llm_calls": calls}

    def _verify(self, state: _State) -> dict:
        """Refuse an answer that cites nothing resolvable.

        The strategy change described at the top of this file. An answer with no
        citation resolving to a retrieved chunk is not demonstrably grounded in
        the corpus, and answering anyway is exactly what false_accept_rate
        counts.
        """
        citations = build_citations(state["chunks"], extract_citation_indices(state["answer"]))
        if not citations:
            return {
                "answer": REFUSAL_MESSAGE,
                "citations": [],
                "refused": True,
                "refusal_reason": "unverified_citations",
            }
        return {"citations": citations, "refused": False, "refusal_reason": None}

    # --- routing -----------------------------------------------------------
    def _after_triage(self, state: _State) -> str:
        return END if state["refused"] else "retrieve"

    def _after_grade(self, state: _State) -> str:
        if not state["refused"]:
            return "generate"
        if state["rewrites"] < self._max_rewrites:
            return "rewrite"
        return END

    def _after_generate(self, state: _State) -> str:
        # A soft refusal is already a refusal; there is nothing to verify.
        return END if state["refused"] else "verify"

    def _build(self) -> StateGraph:
        graph = StateGraph(_State)
        graph.add_node("triage", self._triage)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("grade", self._grade)
        graph.add_node("rewrite", self._rewrite)
        graph.add_node("generate", self._generate)
        graph.add_node("verify", self._verify)

        graph.add_edge(START, "triage")
        graph.add_conditional_edges(
            "triage", self._after_triage, {"retrieve": "retrieve", END: END}
        )
        graph.add_edge("retrieve", "grade")
        graph.add_conditional_edges(
            "grade", self._after_grade, {"generate": "generate", "rewrite": "rewrite", END: END}
        )
        graph.add_edge("rewrite", "retrieve")
        graph.add_conditional_edges(
            "generate", self._after_generate, {"verify": "verify", END: END}
        )
        graph.add_edge("verify", END)
        return graph

    # --- the Engine interface ----------------------------------------------
    def answer(self, question: str) -> EngineResult:
        final = self._graph.invoke(
            {
                "question": question,
                "query": question,
                "chunks": [],
                "answer": REFUSAL_MESSAGE,
                "citations": [],
                "refused": False,
                "refusal_reason": None,
                "rewrites": 0,
                "llm_calls": 0,
            }
        )
        return EngineResult(
            answer=final["answer"],
            citations=final["citations"],
            refused=final["refused"],
            refusal_reason=final["refusal_reason"] if final["refused"] else None,
            stats={
                "llm_calls": final["llm_calls"],
                "retrieved": len(final["chunks"]),
                "rewrites": final["rewrites"],
            },
        )
