from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUDIT_REPORT_PATH = Path(__file__).parents[2] / "eval" / "audit_report.json"


@dataclass
class QualityState:
    status: str = "unknown"
    updated_at: str | None = None
    metrics: dict[str, float | int | str] = field(default_factory=dict)
    active_config: dict[str, float | int] = field(default_factory=dict)
    reason: str = "No audit has been run in this process."


_state = QualityState()


def get_quality_state() -> dict:
    state = asdict(_state)
    if state["status"] != "unknown" or not AUDIT_REPORT_PATH.exists():
        return state

    report = json.loads(AUDIT_REPORT_PATH.read_text(encoding="utf-8"))
    return {
        "status": report.get("status", "unknown"),
        "updated_at": None,
        "metrics": report.get("datasets", {}),
        "active_config": report.get("active_config", {}),
        "reason": (
            "Loaded from latest audit_report.json."
            if not report.get("failures")
            else "; ".join(report["failures"])
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
