from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter

from app.core.quality import get_quality_state
from app.core.runtime_config import get_runtime_config

router = APIRouter()


@router.get("/quality")
async def quality() -> dict:
    return get_quality_state()


@router.post("/quality/audit")
async def run_quality_audit(auto_correct: bool = False) -> dict:
    from eval.audit import audit_failures, evaluate_datasets, summarize_reports, try_auto_correct

    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.quality import update_quality_state
    from app.core.vectorstore import QdrantStore

    base_dir = Path(__file__).parents[2] / "eval"
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    k = settings.top_k

    reports = evaluate_datasets(base_dir, embedder, store, k=k)
    failures = audit_failures(reports, k=k)
    corrected = False
    if failures and auto_correct:
        corrected, corrected_reports = try_auto_correct(base_dir, embedder, store, k=k)
        if corrected:
            reports = corrected_reports
            failures = []

    status = "healthy" if not failures else "suspect_overfit"
    payload = {
        "status": status,
        "failures": failures,
        "corrected": corrected,
        "datasets": summarize_reports(reports, k=k),
        "active_config": asdict(get_runtime_config()),
    }
    update_quality_state(
        status=status,
        metrics=payload["datasets"],
        active_config=payload["active_config"],
        reason="Audit passed." if not failures else "; ".join(failures),
    )
    return payload
