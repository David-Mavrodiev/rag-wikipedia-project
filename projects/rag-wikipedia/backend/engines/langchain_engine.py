"""A LangChain engine: an LCEL chain whose middle step is the model choosing a tool.

WHY A THIRD ENGINE. `direct` is a straight line. `langgraph` is a fixed graph:
it grades, rewrites and verifies in an order this codebase decided. Neither
lets the MODEL decide what to do next. That is the one thing an agent adds, and
it is the thing worth measuring, because it is also the thing that costs the
most: every decision is an LLM call.

WHAT THE AGENT MAY DECIDE. Exactly three actions, parsed strictly:

    SEARCH <query>   retrieve again with a different query
    ANSWER           answer from what is already retrieved
    REFUSE           say the corpus does not support an answer

Anything else is treated as ANSWER, because a 3B model under a fixed budget is
more likely to produce malformed output than a useful fourth action, and
silently looping on unparseable text is the failure mode an agent is famous for.
Measured on llama3.2:3b, tool-calling is weak - so the budget is small
(MAX_STEPS) and the fallback is always a real answer or an honest refusal.

WHY NOT langchain's AgentExecutor. It wants a chat model with tool-calling and
would bring a second client for the model this project already talks to through
`app.core.llm`. Wrapping that LLM in a `Runnable` keeps one call path, so a
comparison between engines is a comparison of ORCHESTRATION rather than of two
different ways to reach Ollama. `langchain-core` is already an `agent` extra
dependency, so this engine adds no new package.

Every step calls `app.core`. Nothing here decides what a refusal is, what
evidence is, or how a grounding prompt is built - those have one definition
each, shared by all three engines. This file owns the ORDER, the choice, and
the budget.
"""

from __future__ import annotations

import re

from app.core.citations import build_citations, extract_citation_indices
from app.core.embeddings import Embedder
from app.core.llm import LLM
from app.core.prompt import build_prompt
from app.core.refusal import REFUSAL_MESSAGE, is_refusal
from app.core.retrieval import retrieve
from app.core.stores import VectorStore

from engines.base import Engine, EngineResult

MAX_STEPS = 3

# The agent's own prompt, like the graph engine's REWRITE_PROMPT: it belongs to
# one engine's strategy, not to app/core/prompt.py, which defines how EVERY
# engine asks for a grounded answer.
DECIDE_PROMPT = """\
You are deciding the next step for a question answered only from Wikipedia extracts.

Question: {question}

Extracts currently retrieved:
{titles}

Reply with exactly one line, one of:
SEARCH <a better search query>
ANSWER
REFUSE

Choose SEARCH only if the extracts are about something else entirely.
Choose REFUSE only if the corpus plainly does not cover the question.
"""

_ACTION = re.compile(r"^\s*(SEARCH|ANSWER|REFUSE)\b[:\-]?\s*(.*)$", re.IGNORECASE)


def parse_action(text: str) -> tuple[str, str]:
    """The model's line, as (action, argument). Unparseable means ANSWER.

    Deliberately forgiving about the shape and strict about the vocabulary: a
    small model wanders in formatting long before it invents a new verb.
    """
    for line in (text or "").strip().splitlines():
        match = _ACTION.match(line)
        if match:
            return match.group(1).upper(), match.group(2).strip()
    return "ANSWER", ""


class LangChainEngine(Engine):
    """retrieve -> (model chooses: search again / answer / refuse) -> answer."""

    def __init__(self, embedder: Embedder, store: VectorStore, llm: LLM,
                 max_steps: int = MAX_STEPS) -> None:
        self._embedder = embedder
        self._store = store
        self._llm = llm
        self._max_steps = max_steps
        self._chain = self._build()

    def _build(self):
        """The LCEL chain. Lazy import: langchain-core is an optional extra."""
        from langchain_core.runnables import RunnableLambda

        return (
            RunnableLambda(self._first_retrieval)
            | RunnableLambda(self._agent_loop)
            | RunnableLambda(self._finish)
        )

    # --- steps ----------------------------------------------------------------

    def _first_retrieval(self, question: str) -> dict:
        chunks, is_empty = retrieve(question, self._embedder, self._store)
        return {"question": question, "chunks": chunks, "empty": is_empty,
                "llm_calls": 0, "steps": 0}

    def _agent_loop(self, state: dict) -> dict:
        """At most `max_steps` decisions, each one an LLM call that is counted."""
        if state["empty"]:
            # Nothing retrieved at all: there is nothing for the model to decide
            # between, and a decision call here would cost a call to learn that.
            return state | {"action": "REFUSE", "reason": "no_evidence"}

        for _ in range(self._max_steps):
            titles = "\n".join(
                f"- {chunk.get('title', '')}" for chunk in state["chunks"]) or "- (none)"
            decision = self._llm.generate(
                DECIDE_PROMPT.format(question=state["question"], titles=titles)
            )
            state["llm_calls"] += 1
            state["steps"] += 1
            action, argument = parse_action(decision)

            if action == "SEARCH" and argument:
                chunks, is_empty = retrieve(argument, self._embedder, self._store)
                # A rewrite that retrieves nothing is worse than the question we
                # had: keep the old chunks, same rule as the graph engine's.
                if not is_empty:
                    state["chunks"] = chunks
                continue
            if action == "REFUSE":
                return state | {"action": "REFUSE", "reason": "agent_refused"}
            return state | {"action": "ANSWER", "reason": None}

        # Budget spent on searching: answer with what we have rather than refuse,
        # because the evidence gate already said these chunks are usable.
        return state | {"action": "ANSWER", "reason": None}

    def _finish(self, state: dict) -> EngineResult:
        stats = {"llm_calls": state["llm_calls"], "retrieved": len(state["chunks"]),
                 "agent_steps": state["steps"]}

        if state["action"] == "REFUSE":
            return EngineResult(
                answer=REFUSAL_MESSAGE,
                refused=True,
                refusal_reason=state["reason"],
                stats=stats,
                # Empty for a hard refusal, kept when the agent declined with
                # chunks in hand: "what was in front of it?" is the question.
                context=[] if state["reason"] == "no_evidence" else state["chunks"],
            )

        answer = self._llm.generate(build_prompt(state["question"], state["chunks"]))
        stats["llm_calls"] += 1

        if is_refusal(answer):
            return EngineResult(answer=answer, refused=True, refusal_reason="soft_refusal",
                                stats=stats, context=state["chunks"])

        citations = build_citations(state["chunks"], extract_citation_indices(answer))
        return EngineResult(answer=answer, citations=citations, refused=False,
                            stats=stats, context=state["chunks"])

    # --- the engine contract --------------------------------------------------

    def answer(self, question: str) -> EngineResult:
        return self._chain.invoke(question)
