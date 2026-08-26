from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUDIT_REPORT_PATH = Path(__file__).parents[2] / "eval" / "audit_report.json"

logger = logging.getLogger(__name__)


@dataclass
class QualityState:
    status: str = "unknown"
    updated_at: str | None = None
    # dataset name -> metric name -> value. Every caller passes
    # summarize_reports(...), which is nested; the old flat annotation was wrong.
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    active_config: dict[str, float | int] = field(default_factory=dict)
    reason: str = "No audit has been run in this process."
    # WHAT was measured. Without this the metrics are unfalsifiable: the same
    # code and the same suites score differently on a 60-article corpus and a
    # 500-article one (golden precision 0.940 vs 0.890, false accepts 0.450 vs
    # 0.500), and a reader could not tell which run a report describes.
    provenance: dict[str, object] = field(default_factory=dict)


# PER PROCESS. With more than one uvicorn worker, POST /quality/audit updates
# only the worker that served it, so GET /quality can answer differently
# depending on which worker responds. The audit_report.json fallback masks this
# only while status is still "unknown". Running multiple workers requires moving
# this into a shared store; the deployment runs a single worker today.
_state = QualityState()


def reset_quality_state() -> None:
    """Restore the pristine state. For tests, which otherwise leak into each other."""
    global _state
    _state = QualityState()


def _git_sha() -> str | None:
    """Best-effort commit hash. None outside a git checkout - never raises."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def build_provenance(*, store=None, top_k: int) -> dict[str, object]:
    """Describe the conditions a set of metrics was measured under.

    Deliberately omits an article count: deriving it means scrolling every
    payload, which is cheap at 6k vectors and not at the 404k the `real` profile
    produces. vector_count is exact and O(1).
    """
    from eval.run_eval import FALSE_ACCEPT_MAX, PRECISION_GATE, RECALL_GATE, REFUSAL_GATE

    from app.core.config import settings

    vector_count = None
    if store is not None:
        try:
            vector_count = store.count()
        except Exception as exc:  # a missing collection must not fail the audit
            logger.warning("Could not read vector count for provenance: %s", exc)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "profile": settings.profile,
        "collection": settings.collection,
        "vector_count": vector_count,
        "embed_model": settings.embed_model,
        "top_k": top_k,
        "gates": {
            "recall": RECALL_GATE,
            "precision": PRECISION_GATE,
            "refusal_accuracy": REFUSAL_GATE,
            "false_accept_rate_max": FALSE_ACCEPT_MAX,
        },
    }


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
        "provenance": report.get("provenance") or {},
    }


def update_quality_state(
    *,
    status: str,
    metrics: dict[str, dict[str, float]],
    active_config: dict[str, float | int],
    reason: str,
    provenance: dict[str, object] | None = None,
) -> None:
    _state.status = status
    _state.updated_at = datetime.now(UTC).isoformat()
    _state.metrics = metrics
    _state.active_config = active_config
    _state.reason = reason
    _state.provenance = provenance or {}
