"""The agent engine: what it may decide, and what it must never decide.

An agent's failure modes are a loop that never ends and a cost nobody counted,
so the budget and the LLM-call accounting are the subject here. The other
subject is the engine contract: the agent chooses the ORDER, never what a
refusal is or what counts as evidence.

The model is a scripted fake throughout - these are about orchestration.
"""

from __future__ import annotations

import pytest
from app.core.refusal import REFUSAL_MESSAGE
from engines.langchain_engine import LangChainEngine, parse_action

pytest.importorskip("langchain_core", reason="the `agent` extra is not installed")

CHUNK = {"title": "Biotite", "text": "Biotite is a mica mineral.", "source_id": "b1",
         "score": 0.9}
OTHER = {"title": "Agate", "text": "Agate is a rock.", "source_id": "a1", "score": 0.8}


class _LLM:
    """Answers from a script; records what it was asked."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "Biotite is a mica [1]."


def _engine(llm, retrieved=None, empty=False, monkeypatch=None, **kwargs):
    calls: list[str] = []

    def fake_retrieve(query, embedder, store, **_):
        calls.append(query)
        return (retrieved if retrieved is not None else [CHUNK]), empty

    monkeypatch.setattr("engines.langchain_engine.retrieve", fake_retrieve)
    engine = LangChainEngine(object(), object(), llm, **kwargs)
    engine._queries = calls
    return engine


# --- parsing ------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("ANSWER", ("ANSWER", "")),
    ("REFUSE", ("REFUSE", "")),
    ("SEARCH biotite mica group", ("SEARCH", "biotite mica group")),
    ("search: biotite", ("SEARCH", "biotite")),
    ("Thinking...\nANSWER", ("ANSWER", "")),
])
def test_the_three_actions_are_recognised(text, expected):
    assert parse_action(text) == expected


@pytest.mark.parametrize("text", ["", "I think I should probably look again", "TOOL_CALL{}"])
def test_anything_unparseable_answers_rather_than_loops(text):
    # A 3B model wanders in formatting long before it invents a new verb, and
    # looping on unparseable text is the classic agent failure.
    assert parse_action(text)[0] == "ANSWER"


# --- the loop and its budget ---------------------------------------------------

def test_answering_at_once_costs_one_decision_and_one_answer(monkeypatch):
    llm = _LLM("ANSWER", "Biotite is a mica mineral [1].")
    result = _engine(llm, monkeypatch=monkeypatch).answer("What is biotite?")

    assert not result.refused
    assert result.stats == {"llm_calls": 2, "retrieved": 1, "agent_steps": 1}
    assert result.citations and result.context == [CHUNK]


def test_a_search_decision_retrieves_again_with_the_model_s_query(monkeypatch):
    llm = _LLM("SEARCH mica minerals", "ANSWER", "Biotite is a mica [1].")
    engine = _engine(llm, monkeypatch=monkeypatch)

    engine.answer("What is that black mineral?")

    assert engine._queries == ["What is that black mineral?", "mica minerals"]


def test_the_budget_is_spent_and_then_it_answers(monkeypatch):
    # Every decision is "search again"; the engine must stop and answer with
    # what it has rather than loop.
    llm = _LLM(*(["SEARCH again"] * 10), "Biotite is a mica [1].")
    result = _engine(llm, monkeypatch=monkeypatch, max_steps=3).answer("q")

    assert result.stats["agent_steps"] == 3
    assert result.stats["llm_calls"] == 4  # 3 decisions + the answer
    assert not result.refused


def test_a_rewrite_that_retrieves_nothing_keeps_the_chunks_it_had(monkeypatch):
    seen = {"n": 0}

    def fake_retrieve(query, embedder, store, **_):
        seen["n"] += 1
        return ([CHUNK], False) if seen["n"] == 1 else ([], True)

    monkeypatch.setattr("engines.langchain_engine.retrieve", fake_retrieve)
    llm = _LLM("SEARCH nonsense", "ANSWER", "Biotite is a mica [1].")
    result = LangChainEngine(object(), object(), llm).answer("q")

    assert result.context == [CHUNK]


# --- the contract --------------------------------------------------------------

def test_an_empty_retrieval_never_asks_the_model_anything(monkeypatch):
    llm = _LLM()
    result = _engine(llm, retrieved=[], empty=True, monkeypatch=monkeypatch).answer("q")

    assert result.refused and result.refusal_reason == "no_evidence"
    assert result.stats["llm_calls"] == 0
    assert llm.prompts == []
    assert result.context == []


def test_the_agent_may_refuse_and_it_is_recorded_as_its_own_reason(monkeypatch):
    result = _engine(_LLM("REFUSE"), monkeypatch=monkeypatch).answer("q")

    assert result.refused and result.refusal_reason == "agent_refused"
    assert result.answer == REFUSAL_MESSAGE
    # What it had in front of it when it declined is kept, as elsewhere.
    assert result.context == [CHUNK]


def test_a_model_refusal_is_still_detected_by_the_shared_rule(monkeypatch):
    # The engine does not decide what a refusal is: app.core.refusal does.
    llm = _LLM("ANSWER", REFUSAL_MESSAGE)
    result = _engine(llm, monkeypatch=monkeypatch).answer("q")

    assert result.refused and result.refusal_reason == "soft_refusal"


def test_the_answer_is_built_with_the_shared_grounding_prompt(monkeypatch):
    llm = _LLM("ANSWER", "Biotite is a mica [1].")
    _engine(llm, retrieved=[CHUNK, OTHER], monkeypatch=monkeypatch).answer("What is biotite?")

    # The second prompt is the one app.core.prompt builds: it carries the chunks.
    assert "Biotite is a mica mineral." in llm.prompts[1]
    assert "Agate is a rock." in llm.prompts[1]
