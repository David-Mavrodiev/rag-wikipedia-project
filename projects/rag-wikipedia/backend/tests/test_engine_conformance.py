"""DirectEngine must return what POST /query returns. Byte for byte.

This is the evidence that introducing the registry changed nothing. It is a
DIFFERENTIAL test on purpose: it never states what the answer should be, it
runs the served handler and the engine over the SAME mocked embedder, store and
model, and asserts the two agree. Expected-value tests drift apart silently
because each side can be updated on its own; this one cannot.

It also matters for what comes after. A second engine is only worth comparing
against `direct` if `direct` is genuinely the thing being served - otherwise a
head-to-head measures two new implementations and says nothing about the system
anyone actually runs.

Every branch in the handler is covered: answered, hard refusal, soft refusal,
and a citation index the model invented.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from app.core.refusal import REFUSAL_MESSAGE
from engines.direct import DirectEngine

GROUNDED = [
    {
        "score": 0.95,
        "text": "Python is a programming language used widely for scripting.",
        "title": "Python",
        "source_id": "1",
    },
    {
        "score": 0.80,
        "text": "Python supports multiple programming paradigms.",
        "title": "Python",
        "source_id": "1",
    },
]


def _both(client, question, chunks, generated):
    """Run the API handler and DirectEngine over identical dependencies.

    The engine is built from the very mock instances the patched factories
    hand the handler, so neither side can be fed something the other was not.
    """
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = chunks
        mock_llm.return_value.generate.return_value = generated

        served = client.post("/query", json={"question": question}).json()
        result = DirectEngine(
            mock_embedder.return_value, mock_store.return_value, mock_llm.return_value
        ).answer(question)

    return served, result


def _assert_identical(served, result):
    assert result.answer == served["answer"]
    assert result.citations == served["citations"]
    assert result.refused == served["refused"]


@pytest.mark.parametrize(
    ("case", "chunks", "generated"),
    [
        # Answered, citing both chunks the model was given.
        ("answered", GROUNDED, "Python is a language [1] with many paradigms [2]."),
        # HARD refusal: nothing retrieved, so the model is never called.
        ("hard_refusal", [], "unused"),
        # SOFT refusal: context was there, the model declined anyway.
        ("soft_refusal", GROUNDED, REFUSAL_MESSAGE),
        # The model invented [7] with two chunks in context. build_citations
        # drops it; both sides must drop it the same way.
        ("invented_citation", GROUNDED, "As described in [7], Python is a language."),
    ],
)
def test_direct_engine_matches_the_served_handler(client, case, chunks, generated):
    served, result = _both(client, "What is Python?", chunks, generated)
    _assert_identical(served, result)


def test_the_answered_case_really_does_produce_citations(client):
    # Guards the parametrised test above from passing vacuously: if a change
    # made BOTH sides return nothing, the equality assertions would still hold.
    served, result = _both(
        client, "What is Python?", GROUNDED, "Python is a language [1] and [2]."
    )
    assert served["refused"] is False
    assert [citation["index"] for citation in result.citations] == [1, 2]


def test_the_hard_refusal_case_really_does_refuse(client):
    # Same guard for the other direction.
    served, result = _both(client, "What is quantum gravity?", [], "unused")
    assert served["refused"] is True
    assert served["answer"] == REFUSAL_MESSAGE
    assert result.stats["llm_calls"] == 0
