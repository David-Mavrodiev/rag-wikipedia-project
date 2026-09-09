"""Serving-latency instrumentation as wired into /query and /metrics.

test_metrics_latency.py covers the recorder in isolation. This covers the part
that is easy to get wrong and impossible to unit-test: that every exit path
through the request handler records a sample, and records it under the right
outcome.
"""

import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from app.core.metrics import recorder

MOCK_CHUNKS = [
    {
        "score": 0.95,
        "text": "Python is a programming language.",
        "title": "Python",
        "source_id": "1",
    }
]


@pytest.fixture(autouse=True)
def _clean_recorder():
    """The recorder is a process-wide singleton, like the quality state.

    Without this, sample counts accumulate across tests and any assertion on
    them passes or fails depending on collection order.
    """
    recorder.reset()
    yield
    recorder.reset()


@contextmanager
def _answering_stack():
    """Patch the three cached dependencies and yield their mocks."""
    with (
        patch("app.api.query._embedder") as embedder,
        patch("app.api.query._store") as store,
        patch("app.api.query._llm") as llm,
    ):
        yield embedder, store, llm


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------
def test_metrics_endpoint_is_public_and_declares_its_scope(client):
    response = client.get("/metrics")
    assert response.status_code == 200

    body = response.json()
    assert body["process_local"] is True
    assert body["percentile_method"] == "nearest-rank"
    assert "process_started_at" in body
    assert "window_per_series" in body


def test_metrics_endpoint_works_before_any_query(client):
    """A fresh process must not 500 on an empty window."""
    body = client.get("/metrics").json()
    assert body["stages"] == {}
    assert body["outcome_counts"] == {"ok": 0, "refused": 0, "error": 0}


def test_metrics_exposes_no_question_or_answer_content(client):
    """It is unauthenticated, so it must carry timings only."""
    with _answering_stack() as (mock_embedder, mock_store, mock_llm):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = MOCK_CHUNKS
        mock_llm.return_value.generate.return_value = "Python [1] is great."
        client.post("/query", json={"question": "SENSITIVE_QUESTION_TOKEN"})

    raw = client.get("/metrics").text
    assert "SENSITIVE_QUESTION_TOKEN" not in raw
    assert "Python" not in raw


# --------------------------------------------------------------------------
# outcome classification: the whole point of the segmentation
# --------------------------------------------------------------------------
def test_answered_query_records_ok_with_stage_timings(client):
    with _answering_stack() as (mock_embedder, mock_store, mock_llm):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = MOCK_CHUNKS
        mock_llm.return_value.generate.return_value = "Python [1] is great."
        assert client.post("/query", json={"question": "What is Python?"}).status_code == 200

    stages = client.get("/metrics").json()["stages"]
    assert stages["total"]["ok"]["samples"] == 1
    # The decomposition is the reason to instrument at all.
    for stage in ("embed", "search", "retrieve", "generate"):
        assert stages[stage]["ok"]["samples"] == 1, f"{stage} was not timed"


def test_dependency_construction_is_not_charged_to_retrieval(client):
    """Cold start must be its own stage, not smeared into `retrieve`.

    Regression guard. The dependencies were originally resolved as arguments
    to the timed retrieve() call, so the one-time 41 s construction of the
    SentenceTransformer landed in `retrieve` and the endpoint reported a
    42-second retrieval that never happened.
    """
    slow_construction_ms = 0.25

    def slow_embedder():
        time.sleep(slow_construction_ms)
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 384
        return embedder

    with (
        patch("app.api.query._embedder", side_effect=slow_embedder),
        patch("app.api.query._store") as store,
        patch("app.api.query._llm") as llm,
    ):
        store.return_value.search.return_value = MOCK_CHUNKS
        llm.return_value.generate.return_value = "Python [1] is great."
        client.post("/query", json={"question": "What is Python?"})

    stages = client.get("/metrics").json()["stages"]
    construction_ms = slow_construction_ms * 1000
    assert stages["deps"]["ok"]["max_ms"] >= construction_ms, "cold start must be measured"
    assert stages["retrieve"]["ok"]["max_ms"] < construction_ms, (
        "construction cost leaked into the retrieve timing"
    )


def test_refusal_is_not_counted_as_ok(client):
    """A 200 ms refusal in the `ok` bucket would flatter the reported latency."""
    with _answering_stack() as (mock_embedder, mock_store, _):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = []
        response = client.post("/query", json={"question": "What is quantum gravity?"})
        assert response.status_code == 200

    counts = client.get("/metrics").json()["outcome_counts"]
    assert counts["refused"] == 1
    assert counts["ok"] == 0


def test_hard_refusal_records_no_generate_stage(client):
    """It never reaches the LLM, so a generate sample would be fabricated."""
    with _answering_stack() as (mock_embedder, mock_store, _):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = []
        client.post("/query", json={"question": "What is quantum gravity?"})

    stages = client.get("/metrics").json()["stages"]
    assert "generate" not in stages


def test_soft_refusal_is_counted_as_refused_not_ok(client):
    """The model declined despite having context: still a refusal."""
    with _answering_stack() as (mock_embedder, mock_store, mock_llm):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = MOCK_CHUNKS
        mock_llm.return_value.generate.return_value = (
            "I don't know based on the provided context."
        )
        client.post("/query", json={"question": "What is Python?"})

    counts = client.get("/metrics").json()["outcome_counts"]
    assert counts["refused"] == 1
    assert counts["ok"] == 0
    # It did call the LLM, so that time is real and must be kept.
    assert client.get("/metrics").json()["stages"]["generate"]["refused"]["samples"] == 1


# --------------------------------------------------------------------------
# the finally guarantee: failures are the samples worth having
# --------------------------------------------------------------------------
def test_vector_store_failure_is_timed_as_error(client):
    with _answering_stack() as (mock_embedder, mock_store, _):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.side_effect = RuntimeError("qdrant down")
        assert client.post("/query", json={"question": "What is Python?"}).status_code == 503

    body = client.get("/metrics").json()
    assert body["outcome_counts"]["error"] == 1
    # The failed search still took time, and that sample is the interesting one.
    assert body["stages"]["search"]["error"]["samples"] == 1


def test_llm_failure_is_timed_as_error(client):
    with _answering_stack() as (mock_embedder, mock_store, mock_llm):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = MOCK_CHUNKS
        mock_llm.return_value.generate.side_effect = RuntimeError("ollama down")
        assert client.post("/query", json={"question": "What is Python?"}).status_code == 503

    body = client.get("/metrics").json()
    assert body["outcome_counts"]["error"] == 1
    assert body["stages"]["generate"]["error"]["samples"] == 1


def test_validation_failure_records_nothing(client):
    """422 is rejected by FastAPI before the handler runs.

    Pinning this so the count is understood rather than assumed: /metrics
    describes work the service actually did.
    """
    assert client.post("/query", json={"question": ""}).status_code == 422

    counts = client.get("/metrics").json()["outcome_counts"]
    assert counts == {"ok": 0, "refused": 0, "error": 0}
