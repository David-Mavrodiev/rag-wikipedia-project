from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
from app.core import rate_limit, tracing
from app.core.config import settings
from app.core.logging import JsonFormatter, TraceContextFilter
from app.core.refusal import REFUSAL_MESSAGE
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision

QUESTION = "What is Python?"

CHUNK = {
    "score": 0.95,
    "text": "Python is a high-level programming language known for readable syntax.",
    "title": "Python",
    "source_id": "1",
}

# A TracerProvider has no API for removing a span processor, so one exporter is
# installed once and cleared per test rather than stacking a new processor for
# every test that asks for spans.
_EXPORTER = InMemorySpanExporter()
_INSTALLED = False


class FlakySpanExporter(SpanExporter):
    """Stands in for a collector that has gone away."""

    fail = False

    def export(self, spans) -> SpanExportResult:
        if self.fail:
            raise RuntimeError("collector is down")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


@pytest.fixture
def spans():
    global _INSTALLED
    if not _INSTALLED:
        # Fails loudly if tracing was never configured, which is the point:
        # a no-op provider would silently make every assertion below vacuous.
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(_EXPORTER))
        _INSTALLED = True
    _EXPORTER.clear()
    return _EXPORTER


def _by_name(exporter: InMemorySpanExporter) -> dict[str, object]:
    return {span.name: span for span in exporter.get_finished_spans()}


def _post_query(client, *, answer: str = "Python [1] is a language.", question: str = QUESTION):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = [CHUNK]
        mock_llm.return_value.generate.return_value = answer
        return client.post("/query", json={"question": question})


def test_query_emits_the_rag_span_tree(client, spans):
    response = _post_query(client)

    assert response.status_code == 200
    finished = _by_name(spans)
    assert {
        "rag.retrieve",
        "rag.embed",
        "rag.search",
        "rag.rerank",
        "rag.token_budget",
        "rag.generate",
        "rag.citations",
        "POST /query",
    } <= set(finished)

    server = finished["POST /query"]
    retrieve = finished["rag.retrieve"]
    embed = finished["rag.embed"]

    # The tree, not just the set: a flat list of spans cannot attribute latency.
    assert retrieve.parent.span_id == server.context.span_id
    assert embed.parent.span_id == retrieve.context.span_id
    assert server.attributes["rag.refused"] is False
    assert server.attributes["rag.citations_count"] == 1


def test_hard_refusal_is_recorded_and_skips_generation(client, spans):
    response = _post_query(client, question="What is my password?")

    assert response.status_code == 200
    assert response.json()["refused"] is True

    finished = _by_name(spans)
    assert finished["POST /query"].attributes["rag.refusal_kind"] == "hard"
    assert finished["rag.retrieve"].attributes["rag.refused"] is True
    # The whole point of a hard refusal is that the expensive stages never run.
    assert "rag.generate" not in finished
    assert "rag.embed" not in finished


def test_soft_refusal_is_distinguishable_from_a_grounded_answer(client, spans):
    response = _post_query(client, answer=REFUSAL_MESSAGE)

    assert response.status_code == 200
    finished = _by_name(spans)
    assert finished["POST /query"].attributes["rag.refusal_kind"] == "soft"
    assert "rag.generate" in finished


def test_question_text_is_not_exported_by_default(client, spans):
    assert settings.trace_capture_content is False

    _post_query(client)

    retrieve = _by_name(spans)["rag.retrieve"]
    assert QUESTION not in str(retrieve.attributes.values())
    assert retrieve.attributes["rag.question_hash"]
    assert retrieve.attributes["rag.question_length"] == len(QUESTION)


def test_question_text_is_exported_when_capture_is_enabled(client, spans, monkeypatch):
    monkeypatch.setattr(settings, "trace_capture_content", True)

    _post_query(client)

    assert _by_name(spans)["rag.retrieve"].attributes["rag.question"] == QUESTION


def test_health_is_not_traced(client, spans):
    response = client.get("/health")

    assert response.status_code == 200
    assert spans.get_finished_spans() == ()


def test_rate_limited_request_is_still_traced(client, spans, monkeypatch):
    async def denied(client_key: str) -> rate_limit.RateLimitDecision:
        return rate_limit.RateLimitDecision(
            allowed=False,
            limit=1,
            remaining=0,
            retry_after_seconds=60,
            reset_after_seconds=60,
        )

    settings.rate_limit_enabled = True
    monkeypatch.setattr(rate_limit, "check_rate_limit", denied)

    response = client.post("/query", json={"question": QUESTION})

    assert response.status_code == 429
    # Would fail if tracing were registered before the rate limiter instead of
    # after it, because the server span would not wrap the rejection.
    server = _by_name(spans)["POST /query"]
    assert server.attributes["rag.rate_limited"] is True
    assert server.attributes["rag.client_hash"]


def test_trace_id_is_returned_to_the_caller(client, spans):
    response = _post_query(client)

    trace_id = response.headers[tracing.TRACE_ID_HEADER]
    server = _by_name(spans)["POST /query"]
    assert trace_id == trace.format_trace_id(server.context.trace_id)


def test_inbound_traceparent_continues_the_trace(client, spans):
    incoming = "abcdefabcdefabcdefabcdefabcdefab"
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = [CHUNK]
        mock_llm.return_value.generate.return_value = "Python [1] is a language."
        response = client.post(
            "/query",
            json={"question": QUESTION},
            headers={"traceparent": f"00-{incoming}-1234567890abcdef-01"},
        )

    assert response.status_code == 200
    # The caller's trace id is adopted, which is what makes browser-to-API
    # correlation work; only the sampling decision is retaken (see below).
    assert response.headers[tracing.TRACE_ID_HEADER] == incoming
    assert trace.format_trace_id(_by_name(spans)["POST /query"].context.trace_id) == incoming


def test_remote_sampled_flag_does_not_force_collection(monkeypatch):
    monkeypatch.setattr(settings, "trace_sample_ratio", 0.0)
    sampler = tracing._build_sampler()

    remote_parent = trace.set_span_in_context(
        trace.NonRecordingSpan(
            trace.SpanContext(
                trace_id=0x1234,
                span_id=0x5678,
                is_remote=True,
                trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
            )
        )
    )
    result = sampler.should_sample(remote_parent, 0x1234, "POST /query", trace.SpanKind.SERVER)

    assert result.decision is Decision.DROP


def test_exporter_failure_does_not_fail_the_request(client):
    exporter = FlakySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    exporter.fail = True
    try:
        response = _post_query(client)
    finally:
        exporter.fail = False

    assert response.status_code == 200


def test_log_records_carry_the_active_trace_id():
    record = logging.LogRecord(
        name="app.api.query",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hi",
        args=(),
        exc_info=None,
    )
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("unit") as span:
        TraceContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))

        assert payload["trace_id"] == trace.format_trace_id(span.context.trace_id)
        assert payload["message"] == "hi"
