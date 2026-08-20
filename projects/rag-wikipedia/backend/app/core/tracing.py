from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace.sampling import Sampler

logger = logging.getLogger(__name__)

# Probe traffic. The platform hits /health on an interval forever, so tracing it
# would dominate span volume while answering nothing. The ASGI instrumentation
# matches this as a substring, which also covers any future /health/* variant.
EXCLUDED_URLS = "health"

# Handed back so a user can quote it in a bug report. Only readable by browser
# JavaScript if it is also listed in CORSMiddleware's expose_headers - the
# header is on the wire either way, which is what makes that omission so easy
# to miss.
TRACE_ID_HEADER = "X-Trace-Id"

_configured = False


def _build_sampler() -> Sampler:
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased

    ratio = settings.trace_sample_ratio
    root = ALWAYS_ON if ratio >= 1.0 else TraceIdRatioBased(ratio)

    # A traceparent arriving from a browser is caller-controlled. Honouring its
    # sampled flag hands any client a switch that forces 100% collection - a
    # cost lever, and the same class of trust mistake as letting a request
    # header decide rate-limit admission. The incoming trace id is still
    # adopted, so browser-to-API correlation survives; only the sampling
    # decision is retaken here.
    #
    # LOCAL parents keep their decision (ParentBased's defaults), or a trace
    # would record only some of its own spans.
    return ParentBased(
        root=root,
        remote_parent_sampled=root,
        remote_parent_not_sampled=root,
    )


def _instrument_libraries(app: FastAPI) -> None:
    """Server spans for ASGI, client spans for Qdrant/Ollama/Redis.

    Instrumenting the libraries rather than wrapping QdrantStore and OllamaLLM
    means a later provider swap inherits tracing instead of needing new code.
    The instrumentation packages are pre-1.0 and versioned separately from the
    SDK, so a partial install is a real possibility - and a telemetry package
    must never be the reason the API fails to boot.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
    except ImportError as exc:
        logger.warning("Auto-instrumentation unavailable (%s); manual spans only", exc)
        return

    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()


def configure_tracing(app: FastAPI) -> None:
    """Install the tracing SDK. Must be called AFTER every other middleware.

    Starlette makes the most recently added middleware the outermost one, so
    calling this last is what puts the server span outside the rate limiter -
    without which every 429 is invisible to tracing, and rejected traffic is
    exactly what gets investigated.

    Disabled by default. With no SDK installed the OpenTelemetry API resolves to
    no-op implementations, so every span in app/core costs nothing until this
    runs; that is what allowed the instrumentation to be written before the
    backend question was settled.
    """
    global _configured

    if not settings.tracing_enabled:
        logger.info("Tracing disabled; OpenTelemetry API is a no-op")
        return

    if _configured:
        return

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.trace_service_name}),
        sampler=_build_sampler(),
    )

    # Batched, and therefore out-of-band: a dead collector must cost dropped
    # spans, never request latency. Neither exporter configured is a valid
    # state - spans are still created and sampled, they just go nowhere - which
    # is what tests attach their own processor to.
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
        )
    if settings.trace_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # Added BEFORE the ASGI instrumentation below so it ends up INSIDE the
    # server span: the header is read from the active span context, and there
    # is no active span outside it.
    app.add_middleware(TraceIdHeaderMiddleware)
    _instrument_libraries(app)

    _configured = True
    logger.info(
        "Tracing enabled service=%s sample_ratio=%s otlp=%s capture_content=%s",
        settings.trace_service_name,
        settings.trace_sample_ratio,
        settings.otlp_endpoint or "none",
        settings.trace_capture_content,
    )


def record_question(span: trace.Span, question: str) -> None:
    """Attach the question as metadata, and as text only when explicitly allowed.

    The question is arbitrary text typed by a member of the public, and a
    telemetry backend is a third party with a retention policy nobody on this
    project reviewed. Hashing keeps "which question is slow" and "which question
    always refuses" answerable without exporting what anyone typed - identical
    questions still group together. Same hash shape as the rate limiter's client
    key so the two are reasoned about the same way.
    """
    if not span.is_recording():
        return

    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]
    span.set_attribute("rag.question_hash", digest)
    span.set_attribute("rag.question_length", len(question))
    if settings.trace_capture_content:
        span.set_attribute("rag.question", question)


class TraceIdHeaderMiddleware:
    """Echo the active trace id on every response, including error responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                span_context = trace.get_current_span().get_span_context()
                if span_context.is_valid:
                    headers = list(message.get("headers", []))
                    headers.append(
                        (
                            TRACE_ID_HEADER.lower().encode("latin-1"),
                            trace.format_trace_id(span_context.trace_id).encode("latin-1"),
                        )
                    )
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
