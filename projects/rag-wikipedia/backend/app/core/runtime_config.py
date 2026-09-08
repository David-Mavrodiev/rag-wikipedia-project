from __future__ import annotations

import json
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path

from app.core.config import settings
from app.core.fileio import atomic_write_text

# Anchored to this file, never to the process working directory. As a relative
# path this landed in a different place depending on where uvicorn was started,
# so a persisted auto-correction could silently fail to load on the next boot.
CONFIG_PATH = Path(__file__).resolve().parents[2] / "eval" / "runtime_config.json"

# Same bounds the environment ingress enforces in app.core.config. Kept here so
# a hand-edited or truncated runtime_config.json cannot install values that
# Settings would have rejected.
_BOUNDS: dict[str, tuple[float, float]] = {
    "refusal_min_score": (0.0, 1.0),
    "refusal_high_confidence_score": (0.0, 1.0),
    "refusal_min_margin": (0.0, 1.0),
    "refusal_min_overlap_terms": (0, 50),
    "refusal_min_evidence_coverage": (0.0, 1.0),
    "retrieval_candidate_k": (1, 1000),
}


@dataclass(frozen=True)
class RetrievalRuntimeConfig:
    refusal_min_score: float
    refusal_high_confidence_score: float
    refusal_min_margin: float
    refusal_min_overlap_terms: int
    retrieval_candidate_k: int
    # Defaulted, and LAST for that reason. A runtime_config.json written before
    # this field existed must still load: it was persisted by an operator's
    # auto-correction, and refusing to parse it would take the API down at
    # startup over a field whose absence has an obvious meaning. Absent = the
    # coverage gate had not been configured = off.
    refusal_min_evidence_coverage: float = 0.0


def validate_runtime_config(config: RetrievalRuntimeConfig) -> RetrievalRuntimeConfig:
    """Return *config* unchanged, or raise ValueError describing the first fault."""
    for name, (low, high) in _BOUNDS.items():
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if not low <= value <= high:
            raise ValueError(f"{name}={value} is outside the allowed range [{low}, {high}]")
    return config


def default_runtime_config() -> RetrievalRuntimeConfig:
    return RetrievalRuntimeConfig(
        refusal_min_score=settings.refusal_min_score,
        refusal_high_confidence_score=settings.refusal_high_confidence_score,
        refusal_min_margin=settings.refusal_min_margin,
        refusal_min_overlap_terms=settings.refusal_min_overlap_terms,
        refusal_min_evidence_coverage=settings.refusal_min_evidence_coverage,
        retrieval_candidate_k=settings.retrieval_candidate_k,
    )


_active_config = default_runtime_config()
_previous_config: RetrievalRuntimeConfig | None = None


def get_runtime_config() -> RetrievalRuntimeConfig:
    return _active_config


def apply_runtime_config(config: RetrievalRuntimeConfig) -> None:
    global _active_config, _previous_config
    validate_runtime_config(config)
    _previous_config = _active_config
    _active_config = config


def rollback_runtime_config() -> bool:
    global _active_config, _previous_config
    if _previous_config is None:
        return False
    _active_config, _previous_config = _previous_config, None
    return True


def save_runtime_config(path: Path = CONFIG_PATH) -> None:
    atomic_write_text(path, json.dumps(asdict(_active_config), indent=2))


def load_runtime_config(path: Path = CONFIG_PATH) -> RetrievalRuntimeConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")

    known = {field.name for field in fields(RetrievalRuntimeConfig)}
    # Only fields WITHOUT a default are required. A field added after a config
    # was written is absent for a knowable reason, and its default is the
    # correct value for that file; a field that never had a default is a real
    # omission and still raises.
    required = {
        field.name
        for field in fields(RetrievalRuntimeConfig)
        if field.default is MISSING and field.default_factory is MISSING
    }
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"{path} is missing {missing}")

    # Only known keys: an unrecognised entry is ignored rather than raising
    # TypeError from the constructor.
    return validate_runtime_config(
        RetrievalRuntimeConfig(**{key: data[key] for key in known if key in data})
    )
