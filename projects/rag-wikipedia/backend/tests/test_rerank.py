"""The relevance gate: what a cross-encoder adds, and what it must never break.

The gate exists for one measured failure: 16 of the 20 held-out unanswerable
questions clear the IDF coverage gate because a NEIGHBOURING article supplies
the question's rare words. These tests pin the behaviour that makes that
fixable - and the behaviour that keeps a missing model from taking the service
down.

No model is downloaded here. A cross-encoder in CI would be a 90 MB download to
test arithmetic and an ordering decision.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest
from app.core import rerank
from app.core.config import settings
from app.core.refusal import decide_evidence
from app.core.runtime_config import (
    apply_runtime_config,
    default_runtime_config,
    get_runtime_config,
)


class _Fake:
    """A cross-encoder whose score is dictated by the test."""

    def __init__(self, scores, raises=False):
        self.scores = scores
        self.raises = raises
        self.pairs = None

    def predict(self, pairs):
        self.pairs = pairs
        if self.raises:
            raise RuntimeError("no")
        return [self.scores[text] for _, text in pairs]


def _reload_runtime():
    """Pick up a monkeypatched setting. The runtime config is read once at
    import, which is what lets an operator tune it without a restart - and what
    makes a test that only patches `settings` silently measure the old value."""
    apply_runtime_config(default_runtime_config())


@pytest.fixture(autouse=True)
def _clean():
    rerank.reset_rerank_state()
    _reload_runtime()
    yield
    rerank.reset_rerank_state()
    _reload_runtime()


def _results(*texts):
    return [{"text": t, "score": 0.9, "title": t} for t in texts]


# --- off by default -----------------------------------------------------------

def test_no_model_means_not_measured(monkeypatch):
    # The difference that matters: None is "not measured", not "nothing is
    # relevant". A list of zeros here would refuse every question.
    monkeypatch.setattr(settings, "rerank_model", "")
    assert rerank.relevance_scores("q", _results("a")) is None
    assert rerank.enabled() is False


def test_nothing_is_loaded_when_it_is_off(monkeypatch):
    monkeypatch.setattr(settings, "rerank_model", "")
    called = []
    monkeypatch.setattr(rerank, "_load", lambda: called.append(1))
    rerank.relevance_scores("q", _results("a"))
    assert not called, "an unconfigured reranker must not import or load anything"


# --- scoring ------------------------------------------------------------------

def test_scores_are_squashed_into_zero_to_one(monkeypatch):
    monkeypatch.setattr(settings, "rerank_model", "fake")
    monkeypatch.setattr(rerank, "_load", lambda: _Fake({"a": 8.0, "b": -8.0}))
    high, low = rerank.relevance_scores("q", _results("a", "b"))
    assert 0.99 < high <= 1.0
    assert 0.0 <= low < 0.01


def test_the_query_is_paired_with_every_passage(monkeypatch):
    monkeypatch.setattr(settings, "rerank_model", "fake")
    fake = _Fake({"a": 1.0, "b": 1.0})
    monkeypatch.setattr(rerank, "_load", lambda: fake)
    rerank.relevance_scores("who is X", _results("a", "b"))
    assert fake.pairs == [("who is X", "a"), ("who is X", "b")]


# --- failure is open, and loud ------------------------------------------------

def test_a_broken_model_falls_back_instead_of_failing_the_request(monkeypatch, caplog):
    monkeypatch.setattr(settings, "rerank_model", "fake")
    monkeypatch.setattr(rerank, "_load", lambda: _Fake({}, raises=True))
    with caplog.at_level(logging.ERROR):
        assert rerank.relevance_scores("q", _results("a")) is None
    assert any("falling back" in r.message for r in caplog.records), (
        "a quality filter may fail open, but never silently"
    )


def test_a_model_that_will_not_load_is_not_retried(monkeypatch, caplog):
    # sentence_transformers is FAKED rather than given a bad model name: the
    # real one would reach the Hugging Face Hub, and a test that needs the
    # internet to prove an offline fallback is not a test of the fallback.
    class _Boom:
        def __init__(self, *a, **k):
            raise OSError("not on disk and not downloadable")

    fake_lib = types.SimpleNamespace(CrossEncoder=_Boom)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_lib)
    monkeypatch.setattr(settings, "rerank_model", "does-not-exist-anywhere")
    with caplog.at_level(logging.ERROR):
        assert rerank.relevance_scores("q", _results("a")) is None
        assert rerank.relevance_scores("q", _results("a")) is None
    assert sum("could not be loaded" in r.message for r in caplog.records) == 1, (
        "a failed load must be reported once, not once per request"
    )


# --- the gate -----------------------------------------------------------------

def test_a_passage_that_is_not_about_the_question_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "rerank_min_relevance", 0.5)
    _reload_runtime()
    results = _results("a")
    results[0]["relevance"] = 0.1
    decision = decide_evidence("what is the melting point of gallium", results)
    assert decision.refused
    assert decision.reason == "low_relevance"
    assert decision.relevance == 0.1


def test_a_relevant_passage_is_judged_by_the_existing_tests(monkeypatch):
    monkeypatch.setattr(settings, "rerank_min_relevance", 0.5)
    _reload_runtime()
    results = _results("gallium melts at 29.76 C")
    results[0]["relevance"] = 0.9
    decision = decide_evidence("what is the melting point of gallium", results)
    assert decision.reason != "low_relevance"
    assert decision.relevance == 0.9


def test_the_high_confidence_escape_hatch_cannot_override_low_relevance(monkeypatch):
    # The ordering IS the intervention. The false accepts this gate exists for
    # are confident vector matches on a neighbouring article, and the escape
    # hatch is what lets them through today.
    monkeypatch.setattr(settings, "rerank_min_relevance", 0.5)
    _reload_runtime()
    results = [{"text": "a neighbouring article", "score": 0.99, "relevance": 0.05}]
    assert decide_evidence("q about something else", results).reason == "low_relevance"


def test_an_unmeasured_relevance_changes_nothing(monkeypatch):
    # Every deployment that has not configured a reranker must behave exactly as
    # it did before this file existed.
    monkeypatch.setattr(settings, "rerank_min_relevance", 0.5)
    _reload_runtime()
    decision = decide_evidence("what is the melting point of gallium", _results("gallium melts"))
    assert decision.reason != "low_relevance"
    assert decision.relevance == -1.0


def test_the_threshold_is_a_runtime_tunable():
    # So a sweep can fit it without a code change, and so the audit records it.
    assert "rerank_min_relevance" in get_runtime_config().__dataclass_fields__
