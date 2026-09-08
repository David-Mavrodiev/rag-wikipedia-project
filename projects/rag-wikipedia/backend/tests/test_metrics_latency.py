"""Tests for the serving-latency recorder.

Named test_metrics_latency to avoid colliding with test_metrics.py, which
covers the *retrieval quality* metrics (recall, MRR, refusal accuracy). Two
different meanings of "metrics" live in this project and keeping them in one
file would confuse both.
"""

from __future__ import annotations

import threading

import pytest
from app.core.metrics import (
    MIN_SAMPLES_P50,
    MIN_SAMPLES_P95,
    LatencyRecorder,
    percentile,
)


# --------------------------------------------------------------------------
# percentile: verified by hand, because a percentile that is subtly wrong is
# worse than none - it looks authoritative.
# --------------------------------------------------------------------------
def test_percentile_nearest_rank_matches_hand_calculation():
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    # nearest-rank: ceil(n * q / 100) -> the rank-th smallest, 1-indexed.
    assert percentile(values, 50) == 50.0   # ceil(10*50/100) = 5 -> 5th = 50
    assert percentile(values, 95) == 100.0  # ceil(10*95/100) = 10 -> 10th = 100
    assert percentile(values, 100) == 100.0


def test_percentile_returns_an_observed_value():
    """Nearest-rank must never interpolate a value that was not measured."""
    values = [1.0, 2.0, 100.0]
    for q in (1, 25, 50, 75, 95, 99, 100):
        assert percentile(values, q) in values


def test_percentile_single_sample():
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 95) == 42.0


def test_percentile_low_quantile_does_not_underflow_rank():
    """q small enough to floor the rank to 0 must still return the minimum."""
    assert percentile([5.0, 6.0, 7.0], 1) == 5.0


# --------------------------------------------------------------------------
# sample thresholds: the module's core honesty claim
# --------------------------------------------------------------------------
def test_p95_is_none_below_threshold_and_says_why():
    rec = LatencyRecorder()
    for i in range(MIN_SAMPLES_P95 - 1):
        rec.record("total", "ok", float(i))

    entry = rec.snapshot()["stages"]["total"]["ok"]
    assert entry["p95_ms"] is None, "p95 from a short window must not be reported"
    assert "insufficient samples for p95" in entry["note"]
    assert entry["p50_ms"] is not None, "p50 survives a much smaller sample"


def test_p50_is_none_below_its_own_threshold():
    rec = LatencyRecorder()
    for _ in range(MIN_SAMPLES_P50 - 1):
        rec.record("total", "ok", 1.0)

    entry = rec.snapshot()["stages"]["total"]["ok"]
    assert entry["p50_ms"] is None
    assert "insufficient samples for p50" in entry["note"]


def test_both_percentiles_reported_once_samples_suffice():
    rec = LatencyRecorder()
    for i in range(MIN_SAMPLES_P95):
        rec.record("total", "ok", float(i))

    entry = rec.snapshot()["stages"]["total"]["ok"]
    assert entry["p50_ms"] is not None
    assert entry["p95_ms"] is not None
    assert "note" not in entry


# --------------------------------------------------------------------------
# outcome segmentation: the reason this module exists
# --------------------------------------------------------------------------
def test_refusals_never_contaminate_the_ok_percentiles():
    """A fast refusal must not drag down the latency reported for answers.

    This is the failure the segmentation prevents: 200 ms refusals pooled with
    3 s answers report a median that describes neither.
    """
    rec = LatencyRecorder()
    for _ in range(MIN_SAMPLES_P95):
        rec.record("total", "ok", 3000.0)
    for _ in range(MIN_SAMPLES_P95):
        rec.record("total", "refused", 200.0)

    stages = rec.snapshot()["stages"]["total"]
    assert stages["ok"]["p50_ms"] == 3000.0
    assert stages["refused"]["p50_ms"] == 200.0


def test_outcome_counts_count_requests_not_stages():
    rec = LatencyRecorder()
    for stage in ("embed", "search", "generate", "total"):
        for _ in range(3):
            rec.record(stage, "ok", 1.0)

    # Four stages recorded per request, but three requests happened.
    assert rec.snapshot()["outcome_counts"]["ok"] == 3


def test_error_outcome_is_recorded():
    """A 503's latency is more interesting than a success's, not less."""
    rec = LatencyRecorder()
    rec.record("total", "error", 55.0)
    assert rec.snapshot()["stages"]["total"]["error"]["samples"] == 1


# --------------------------------------------------------------------------
# robustness: this runs in a finally block on the serving path
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stage,outcome",
    [("typo", "ok"), ("total", "typo"), ("", ""), ("TOTAL", "ok")],
)
def test_unknown_keys_are_dropped_never_raised(stage, outcome):
    """A typo must not turn a served response into a 500."""
    rec = LatencyRecorder()
    rec.record(stage, outcome, 1.0)
    assert rec.snapshot()["stages"] == {}


def test_window_is_bounded():
    rec = LatencyRecorder(window=10)
    for i in range(100):
        rec.record("total", "ok", float(i))

    entry = rec.snapshot()["stages"]["total"]["ok"]
    assert entry["samples"] == 10
    assert entry["min_ms"] == 90.0, "oldest samples must be evicted, not newest"


def test_empty_recorder_snapshots_without_crashing():
    snap = LatencyRecorder().snapshot()
    assert snap["stages"] == {}
    assert snap["outcome_counts"] == {"ok": 0, "refused": 0, "error": 0}


def test_concurrent_records_lose_nothing():
    """deque.append is atomic in CPython, but the get-or-create is not."""
    rec = LatencyRecorder(window=10_000)

    def worker():
        for _ in range(200):
            rec.record("total", "ok", 1.0)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert rec.snapshot()["stages"]["total"]["ok"]["samples"] == 1600


# --------------------------------------------------------------------------
# provenance: the number must carry its own scope
# --------------------------------------------------------------------------
def test_snapshot_declares_its_limits():
    snap = LatencyRecorder(window=64).snapshot()
    assert snap["process_local"] is True, "a process p95 is not a fleet p95"
    assert snap["percentile_method"] == "nearest-rank"
    assert snap["window_per_series"] == 64
    assert snap["process_started_at"].endswith("+00:00"), "timestamp must be tz-aware UTC"


def test_reset_clears_samples_but_keeps_provenance():
    rec = LatencyRecorder()
    rec.record("total", "ok", 1.0)
    rec.reset()
    snap = rec.snapshot()
    assert snap["stages"] == {}
    assert snap["process_local"] is True
