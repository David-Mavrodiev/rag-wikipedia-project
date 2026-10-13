"""The latency bench: every sample lands in the group it belongs to.

The failure this guards against already happened once - a cold GPU and a
clamped one reported as one system's "latency". Grouping is the whole job.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bench_latency import (  # noqa: E402
    build_markdown,
    classify,
    parse_server_timing,
    question_pool,
    summarize,
)


def _sample(total=4000.0, deps=0.0, generate=3900.0, status=200, regime="full"):
    timings = {"deps": deps, "embed": 20.0, "search": 30.0, "retrieve": 60.0, "total": total}
    if generate is not None:
        timings["generate"] = generate
    return {"status": status, "timings": timings, "client_ms": total + 5,
            "gpu": {"regime": regime} if regime else None}


def test_server_timing_parses_every_stage():
    header = "deps;dur=0.0, embed;dur=17.2, generate;dur=4276.9, total;dur=4331.9"
    assert parse_server_timing(header) == {
        "deps": 0.0, "embed": 17.2, "generate": 4276.9, "total": 4331.9
    }


def test_a_missing_header_parses_to_nothing():
    assert parse_server_timing(None) == {}


def test_a_request_that_paid_model_loading_is_cold_not_slow():
    # 60.8 s of lazy loading must never be averaged into warm latency.
    assert classify(_sample(total=75_000.0, deps=60_800.0)) == "cold"


def test_a_refusal_before_generation_is_its_own_group():
    assert classify(_sample(total=62.0, generate=None)) == "no_generation"


def test_an_error_is_not_a_latency_sample():
    assert classify(_sample(status=503)) == "error"


def test_generated_requests_split_by_gpu_regime():
    samples = [_sample(total=4000.0 + i) for i in range(5)] + [
        _sample(total=20_000.0, regime="throttled"),
        _sample(total=60.0, generate=None),
    ]
    summary = summarize(samples)
    assert summary["generated"]["total"]["n"] == 6
    assert summary["generated_full"]["total"]["p50_ms"] == 4002.0
    assert summary["generated_throttled"]["total"]["p50_ms"] is None  # one sample: range only
    assert summary["no_generation"]["total"]["n"] == 1
    assert summary["generated_stages"]["generate"]["n"] == 6


def test_the_question_pool_never_repeats_a_question():
    pool = question_pool()
    assert len(pool) == len(set(pool)) and len(pool) >= 100


def test_the_report_names_its_conditions():
    report = {
        "url": "http://x", "git_sha": "abc", "n_requests": 1,
        "conditions": {"gpu": "RTX", "pause_at": 90, "resume_at": 80, "total_cooldown_s": 0.0},
        "summary": summarize([_sample()]),
    }
    markdown = build_markdown(report)
    assert "RTX" in markdown and "90/80" in markdown
