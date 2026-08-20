from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings

CONFIG_PATH = Path("backend/eval/runtime_config.json")


@dataclass(frozen=True)
class RetrievalRuntimeConfig:
    refusal_min_score: float
    refusal_high_confidence_score: float
    refusal_min_margin: float
    refusal_min_overlap_terms: int
    retrieval_candidate_k: int


def default_runtime_config() -> RetrievalRuntimeConfig:
    return RetrievalRuntimeConfig(
        refusal_min_score=settings.refusal_min_score,
        refusal_high_confidence_score=settings.refusal_high_confidence_score,
        refusal_min_margin=settings.refusal_min_margin,
        refusal_min_overlap_terms=settings.refusal_min_overlap_terms,
        retrieval_candidate_k=settings.retrieval_candidate_k,
    )


_active_config = default_runtime_config()
_previous_config: RetrievalRuntimeConfig | None = None


def get_runtime_config() -> RetrievalRuntimeConfig:
    return _active_config


def apply_runtime_config(config: RetrievalRuntimeConfig) -> None:
    global _active_config, _previous_config
    _previous_config = _active_config
    _active_config = config


def rollback_runtime_config() -> bool:
    global _active_config, _previous_config
    if _previous_config is None:
        return False
    _active_config, _previous_config = _previous_config, None
    return True


def save_runtime_config(path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(_active_config), indent=2), encoding="utf-8")


def load_runtime_config(path: Path = CONFIG_PATH) -> RetrievalRuntimeConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RetrievalRuntimeConfig(**data)
