from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUDIT_REPORT_PATH = Path(__file__).parents[2] / "eval" / "audit_report.json"

logger = logging.getLogger(__name__)


@dataclass
class QualityState:
    status: str = "unknown"
    updated_at: str | None = None
    metrics: dict[str, float | int | str] = field(default_factory=dict)
    active_config: dict[str, float | int] = field(default_factory=dict)
    reason: str = "No audit has been run in this process."


_state = QualityState()


def _load_audit_report() -> dict | None:
    """Return the last audit report, or None when it cannot be trusted.

    The file is written by the CLI audit in another process. A truncated or
    hand-edited report must degrade this endpoint to "unknown", never turn a
    GET /quality into a 500.
    """
    try:
        report = json.loads(AUDIT_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable %s: %s", AUDIT_REPORT_PATH, exc)
        return None

    if not isinstance(report, dict):
        logger.warning("Ignoring %s: expected a JSON object", AUDIT_REPORT_PATH)
        return None
    return report


def get_quality_state() -> dict:
    state = asdict(_state)
    if state["status"] != "unknown" or not AUDIT_REPORT_PATH.exists():
        return state

    report = _load_audit_report()
    if report is None:
        return state

    failures = report.get("failures") or []
    metrics = report.get("datasets")
    active_config = report.get("active_config")
    return {
        "status": report.get("status", "unknown"),
        "updated_at": None,
        "metrics": metrics if isinstance(metrics, dict) else {},
        "active_config": active_config if isinstance(active_config, dict) else {},
        "reason": (
            "Loaded from latest audit_report.json."
            if not failures
            else "; ".join(str(failure) for failure in failures)
        ),
    }


def update_quality_state(
    *,
    status: str,
    metrics: dict[str, float | int | str],
    active_config: dict[str, float | int],
    reason: str,
) -> None:
    _state.status = status
    _state.updated_at = datetime.now(UTC).isoformat()
    _state.metrics = metrics
    _state.active_config = active_config
    _state.reason = reason
