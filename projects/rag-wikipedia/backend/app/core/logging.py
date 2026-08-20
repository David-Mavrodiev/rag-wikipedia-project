from __future__ import annotations

import json
import logging
import sys

from opentelemetry import trace

from app.core.config import settings

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# LogRecord's own attributes. Anything else on a record arrived through
# `extra=` and belongs in the JSON payload; without this list the two are
# indistinguishable. Derived from a real record so it cannot drift from the
# stdlib's actual field set.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__
) | {"message", "asctime"}


class TraceContextFilter(logging.Filter):
    """Stamp every record with the active trace and span id.

    This is the join between two pipelines that never meet: logs leave through
    stdout to the platform's log sink, spans leave through OTLP to a trace
    backend. The id is the only thing that puts them back together.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record.trace_id = trace.format_trace_id(span_context.trace_id)
            record.span_id = trace.format_span_id(span_context.span_id)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED_RECORD_ATTRS
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str: a log line must never be the thing that raises.
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable, with the trace id appended only when there is one."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        trace_id = getattr(record, "trace_id", None)
        return f"{line} trace_id={trace_id}" if trace_id else line


def configure_logging() -> None:
    """Replace basicConfig so log records carry trace context.

    JSON is queryable but unpleasant to read in a terminal, so the format is a
    deployment choice rather than a global one: text locally, JSON wherever the
    output is being ingested by something.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceContextFilter())
    handler.setFormatter(
        JsonFormatter() if settings.log_format == "json" else TextFormatter(TEXT_FORMAT)
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
