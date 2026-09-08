from __future__ import annotations

from fastapi import APIRouter

from app.core.metrics import recorder

router = APIRouter()


@router.get("/metrics")
async def metrics() -> dict:
    """Serving latency for this process.

    Public and unauthenticated, like `/quality`: it exposes timings, never
    question or answer content. The rate limiter is an allow-list covering
    `/query` only, so this needs no exemption.

    The response carries its own scope - `process_local`, the window size, the
    sample counts and the percentile method - because a percentile published
    without them invites being read as a fleet-wide figure it is not.
    """
    return recorder.snapshot()
