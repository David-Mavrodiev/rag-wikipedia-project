"""The graph engine: routing, the retry budget, and the verify strategy.

These assert the ROUTES, not the answers. What a good answer looks like is the
job of the eval suites; what this engine is for is deciding when to retry, when
to refuse, and what it costs to do either.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.core.refusal import REFUSAL_MESSAGE
from engines.langgraph_engine import LangGraphEngine

GROUNDED = {
    "score": 0.95,
    "text": "Python is a programming language used widely for scripting.",
    "title": "Python",
    "source_id": "1",
}
# Below refusal_min_score (0.45), so decide_evidence refuses on score alone.
TOO_WEAK = {"score": 0.20, "text": "Unrelated text.", "title": "Other", "source_id": "9"}


def _deps(chunks, generated):
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = chunks
    llm = MagicMock()
    llm.generate.return_value = generated
    return embedder, store, llm


def test_a_cited_answer_is_returned_without_any_rewrite():
    engine = LangGraphEngine(*_deps([GROUNDED], "Python is a language [1]."))
    result = engine.answer("What is Python?")

    assert result.refused is False
    assert [c["index"] for c in result.citations] == [1]
    assert result.stats == {"llm_calls": 1, "retrieved": 1, "rewrites": 0}


def test_triage_refuses_before_spending_a_search_or_a_token():
    embedder, store, llm = _deps([GROUNDED], "unused")
    result = LangGraphEngine(embedder, store, llm).answer("What is my bank password?")

    assert result.refused is True
    assert result.refusal_reason == "private_or_time_dependent_question"
    assert result.stats["llm_calls"] == 0
    llm.generate.assert_not_called()
    store.search.assert_not_called()


def test_weak_evidence_is_retried_up_to_the_budget_and_then_refused():
    # Every retrieval comes back too weak, so the engine rewrites twice, gives
    # up, and refuses. The budget is what stops this being an expensive way to
    # reach the same answer.
    embedder, store, llm = _deps([TOO_WEAK], "a rewritten query")
    result = LangGraphEngine(embedder, store, llm, max_rewrites=2).answer("What is Python?")

    assert result.refused is True
    assert result.stats["rewrites"] == 2
    assert result.stats["llm_calls"] == 2          # two rewrites, no generation
    assert store.search.call_count == 3            # the original plus two retries


def test_the_retry_budget_is_configurable_and_respected():
    embedder, store, llm = _deps([TOO_WEAK], "a rewritten query")
    result = LangGraphEngine(embedder, store, llm, max_rewrites=0).answer("What is Python?")

    assert result.refused is True
    assert result.stats["rewrites"] == 0
    llm.generate.assert_not_called()


def test_an_uncited_answer_is_refused_by_verify():
    # THE strategy change. The served pipeline returns this answer; this engine
    # refuses it, on the grounds that nothing in it resolves to a retrieved
    # chunk. Whether that trade is worth making is what the eval measures.
    engine = LangGraphEngine(*_deps([GROUNDED], "Python is a programming language."))
    result = engine.answer("What is Python?")

    assert result.refused is True
    assert result.refusal_reason == "unverified_citations"
    assert result.answer == REFUSAL_MESSAGE
    assert result.citations == []


def test_a_citation_pointing_at_nothing_is_refused_too():
    # [7] with one chunk in context resolves to nothing, so the answer is not
    # demonstrably grounded even though it looks cited.
    engine = LangGraphEngine(*_deps([GROUNDED], "As shown in [7], Python is a language."))
    assert engine.answer("What is Python?").refusal_reason == "unverified_citations"


def test_a_soft_refusal_skips_verification():
    # The model already declined. Verifying citations on a refusal would
    # relabel it with a reason that did not happen.
    engine = LangGraphEngine(*_deps([GROUNDED], REFUSAL_MESSAGE))
    result = engine.answer("What is Python?")

    assert result.refused is True
    assert result.refusal_reason == "soft_refusal"


def test_an_empty_rewrite_does_not_blank_the_query():
    # A model that returns nothing must not turn a weak search into an empty
    # one; the budget should run out honestly instead.
    embedder, store, llm = _deps([TOO_WEAK], "   ")
    LangGraphEngine(embedder, store, llm, max_rewrites=1).answer("What is Python?")

    searched = [call.args[0] for call in embedder.embed.call_args_list]
    assert searched == ["What is Python?", "What is Python?"]
