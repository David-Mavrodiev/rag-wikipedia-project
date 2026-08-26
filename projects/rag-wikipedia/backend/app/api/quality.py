from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict
from pathlib import Path

from eval.run_eval import InvalidGoldenSet
from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.quality import build_provenance, get_quality_state, update_quality_state
from app.core.runtime_config import get_runtime_config

router = APIRouter()

# One audit at a time, per process. try_auto_correct mutates the SHARED runtime
# config through apply/rollback, so two overlapping runs interleave those
# mutations and leave behind a config neither run selected. A second caller gets
# 409 instead of that corruption. (Per process: with multiple workers this needs
# a shared lock, which is a deliberate non-goal at this scale.)
_audit_lock = asyncio.Lock()


@router.get("/quality")
async def quality() -> dict:
    return get_quality_state()


def _require_admin_token(token: str | None) -> None:
    expected = settings.quality_admin_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Auto-correction is disabled. Set QUALITY_ADMIN_TOKEN to enable it.",
        )
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing quality admin token.")


def _run_audit(auto_correct: bool) -> dict:
    """Score every suite. BLOCKING - loads the embedder, hits Qdrant, runs every
    case. Must only ever be called on a worker thread.
    """
    from eval.audit import (
        audit_failures,
        audit_status,
        evaluate_datasets,
        summarize_reports,
        try_auto_correct,
    )

    from app.core.embeddings import BGEEmbedder
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

    status = audit_status(failures)
    update_quality_state(
        status=status,
        metrics=summarize_reports(reports, k=k),
        active_config=asdict(get_runtime_config()),
        reason="Audit passed." if not failures else "; ".join(failures),
        provenance=build_provenance(store=store, top_k=k),
    )
    # Same shape GET /quality returns, plus the two audit-only fields. The two
    # used to disagree - POST returned `datasets` where GET returned `metrics`,
    # so the quality panel's table went blank after a successful audit.
    return {**get_quality_state(), "failures": failures, "corrected": corrected}


@router.post("/quality/audit")
async def run_quality_audit(
    auto_correct: bool = False,
    x_quality_token: str | None = Header(default=None),
) -> dict:
    # Scoring is read-only and stays open so the dashboard button works.
    # auto_correct is not: it rewrites the retrieval thresholds this API serves
    # from and PERSISTS them, so it is gated.
    if auto_correct:
        _require_admin_token(x_quality_token)

    if _audit_lock.locked():
        raise HTTPException(status_code=409, detail="An audit is already running.")

    async with _audit_lock:
        try:
            return await run_in_threadpool(_run_audit, auto_correct)
        except InvalidGoldenSet as exc:
            # A malformed suite is bad input, not a server fault. It used to be
            # SystemExit, which is a BaseException and tore down the request
            # rather than producing a response.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
