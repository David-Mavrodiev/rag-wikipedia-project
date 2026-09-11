"""The engine registry, and what DirectEngine reports beyond the answer.

Equivalence with the served pipeline is asserted separately, in
test_engine_conformance.py. These cover the registry itself and the fields the
HTTP response does not carry - the ones that exist so two engines can be
compared rather than merely swapped.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.core.refusal import REFUSAL_MESSAGE
from engines import ENGINE_REGISTRY, make_engine
from engines.direct import DirectEngine


def _deps(chunks, generated="An answer [1]."):
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = chunks
    llm = MagicMock()
    llm.generate.return_value = generated
    return embedder, store, llm


def _grounded_chunk():
    return {
        "score": 0.95,
        "text": "Python is a programming language.",
        "title": "Python",
        "source_id": "1",
    }


# --- the registry ----------------------------------------------------------
def test_default_engine_is_direct():
    assert make_engine(MagicMock(), MagicMock(), MagicMock()).__class__ is DirectEngine


def test_engine_choice_selects_from_the_environment(monkeypatch):
    monkeypatch.setenv("ENGINE_CHOICE", "direct")
    assert isinstance(make_engine(MagicMock(), MagicMock(), MagicMock()), DirectEngine)


def test_an_explicit_choice_overrides_the_environment(monkeypatch):
    # The eval harness names the engine it is measuring; ambient env must not
    # silently score something else.
    monkeypatch.setenv("ENGINE_CHOICE", "does-not-exist")
    assert isinstance(
        make_engine(MagicMock(), MagicMock(), MagicMock(), choice="direct"), DirectEngine
    )


def test_an_unknown_engine_fails_loudly_and_lists_the_options(monkeypatch):
    monkeypatch.setenv("ENGINE_CHOICE", "langraph")  # a typo, not a framework
    with pytest.raises(ValueError) as caught:
        make_engine(MagicMock(), MagicMock(), MagicMock())

    # The message names the typo AND every real option, so the fix is visible
    # without opening the registry.
    message = str(caught.value)
    assert "langraph" in message
    for name in ENGINE_REGISTRY:
        assert name in message


def test_direct_is_registered():
    # Guards the point of the registry: `direct` is a first-class entry, not a
    # special case the new thing gets bolted on beside.
    assert "direct" in ENGINE_REGISTRY


# --- what DirectEngine reports ---------------------------------------------
def test_an_answered_question_costs_one_llm_call():
    engine = DirectEngine(*_deps([_grounded_chunk()]))
    result = engine.answer("What is Python?")

    assert result.refused is False
    assert result.refusal_reason is None
    assert result.stats == {"llm_calls": 1, "retrieved": 1}


def test_a_hard_refusal_never_reaches_the_model():
    # The cheapest refusal there is, and the reason it is worth measuring:
    # an engine that refuses here spends nothing.
    embedder, store, llm = _deps([])
    result = DirectEngine(embedder, store, llm).answer("What is quantum gravity?")

    assert result.refused is True
    assert result.answer == REFUSAL_MESSAGE
    assert result.refusal_reason == "no_evidence"
    assert result.stats["llm_calls"] == 0
    llm.generate.assert_not_called()


def test_a_soft_refusal_is_billed_and_reported_separately():
    # The model declined despite having context. It cost a call, and the
    # distinction matters: hard and soft refusals have different fixes.
    engine = DirectEngine(*_deps([_grounded_chunk()], generated=REFUSAL_MESSAGE))
    result = engine.answer("What is Python?")

    assert result.refused is True
    assert result.refusal_reason == "soft_refusal"
    assert result.stats["llm_calls"] == 1
    assert result.citations == []


def test_a_refused_answer_carries_no_citations():
    engine = DirectEngine(*_deps([_grounded_chunk()], generated=REFUSAL_MESSAGE))
    assert engine.answer("What is Python?").citations == []


def test_errors_are_not_translated():
    # Mapping a dead vector store to a 503 is the job of the API. The eval
    # harness wants the exception, not a status code.
    embedder, store, llm = _deps([_grounded_chunk()])
    store.search.side_effect = RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        DirectEngine(embedder, store, llm).answer("What is Python?")
