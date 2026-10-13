"""Serving latency, reported with the conditions it was measured under.

This repository once published "3 s" (the demo) and "48 s" (engine_comparison.md)
for the same system. Both were true. The demo ran on a cool GPU; the comparison
ran under sustained load, where the reference laptop's RTX 3060 clamps to
210 MHz of 2,100. A latency number that does not carry its regime is not one.

So every request here records:
  * its stage split, from the API's Server-Timing header (deps, embed, search,
    retrieve, generate, total) - the same timers /metrics aggregates;
  * whether the model ran (a refusal before generation costs ~60 ms and must
    not be averaged with a 4-second answer);
  * whether it was a COLD request (lazy model loading on the first request after
    a start: 60.8 s measured on 2026-09-21), reported apart from warm ones;
  * the GPU regime while it ran - `full` or `throttled` - from eval/thermal.py.

Percentiles follow /metrics: nearest-rank, p50 from 5 samples, p95 from 100.

Questions are DISTINCT, drawn from every committed suite. Repeating a question
lets Ollama reuse the cached prompt prefix: a repeat measured 371 ms against
4.4 s for a new question, which would flatter every percentile.

Usage (API running, e.g. RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0 uvicorn ...):
    uv run python scripts/bench_latency.py --requests 150
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.metrics import MIN_SAMPLES_P50, MIN_SAMPLES_P95, percentile  # noqa: E402
from eval.suites import SUITE_PROFILES, suite_path  # noqa: E402
from eval.thermal import GpuSampler, ThermalAbort, ThermalPacer, gpu_name  # noqa: E402

DEFAULT_OUT = BACKEND / "eval" / "latency" / "serving"
# A request whose dependency construction took this long paid the cold start.
COLD_DEPS_MS = 1000.0
STAGES = ("deps", "embed", "search", "retrieve", "generate", "total")


def question_pool() -> list[str]:
    """Every distinct question across the committed suites, in a fixed order."""
    seen: dict[str, None] = {}
    for name in SUITE_PROFILES:
        path = suite_path(name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.setdefault(json.loads(line)["question"], None)
    return list(seen)


def parse_server_timing(header: str | None) -> dict[str, float]:
    timings: dict[str, float] = {}
    for part in (header or "").split(","):
        name, _, rest = part.strip().partition(";dur=")
        try:
            timings[name] = float(rest)
        except ValueError:
            continue
    return timings


def ask(url: str, question: str, timeout: float) -> tuple[int, dict, dict[str, float]]:
    body = json.dumps({"question": question}).encode()
    request = urllib.request.Request(
        f"{url}/query", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                json.loads(response.read()),
                parse_server_timing(response.headers.get("Server-Timing")),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, {}, {}


def classify(sample: dict) -> str:
    if sample["status"] != 200:
        return "error"
    if sample["timings"].get("deps", 0.0) >= COLD_DEPS_MS:
        return "cold"
    return "generated" if "generate" in sample["timings"] else "no_generation"


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50_ms": percentile(ordered, 50) if len(ordered) >= MIN_SAMPLES_P50 else None,
        "p95_ms": percentile(ordered, 95) if len(ordered) >= MIN_SAMPLES_P95 else None,
        "min_ms": ordered[0] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
    }


def summarize(samples: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for sample in samples:
        kind = classify(sample)
        groups.setdefault(kind, []).append(sample)
        if kind == "generated":
            regime = (sample.get("gpu") or {}).get("regime") or "no_reading"
            groups.setdefault(f"generated_{regime}", []).append(sample)

    summary = {
        name: {
            "total": distribution(
                [s["timings"]["total"] for s in group if "total" in s["timings"]]
            ),
            "client": distribution([s["client_ms"] for s in group]),
        }
        for name, group in groups.items()
    }
    generated = groups.get("generated", [])
    summary["generated_stages"] = {
        stage: distribution([s["timings"][stage] for s in generated if stage in s["timings"]])
        for stage in STAGES
    }
    return summary


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def build_markdown(report: dict) -> str:
    summary = report["summary"]
    cond = report["conditions"]
    lines = [
        "## Serving latency",
        "",
        f"_{report['n_requests']} distinct questions against `{report['url']}` | GPU "
        f"`{cond.get('gpu') or 'no reading'}` | paced at {cond.get('pause_at')}/"
        f"{cond.get('resume_at')} C, cooled {cond.get('total_cooldown_s')} s | "
        f"git `{report.get('git_sha')}`_",
        "",
        "| group | n | p50 ms | p95 ms | min ms | max ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("generated", "generated_full", "generated_throttled", "generated_unknown",
                 "generated_no_reading", "no_generation", "cold", "error"):
        if name in summary:
            d = summary[name]["total"] if name != "error" else summary[name]["client"]
            lines.append(
                f"| {name} | {d['n']} | {_fmt(d['p50_ms'])} | {_fmt(d['p95_ms'])} | "
                f"{_fmt(d['min_ms'])} | {_fmt(d['max_ms'])} |"
            )
    lines += ["", "Stage split of warm generated requests (p50 ms):", ""]
    stages = summary["generated_stages"]
    lines.append(" | ".join(f"{stage} {_fmt(stages[stage]['p50_ms'])}" for stage in STAGES))
    lines += [
        "",
        f"_Server-side `total` from Server-Timing. p50 needs {MIN_SAMPLES_P50} samples, p95 "
        f"{MIN_SAMPLES_P95}; smaller groups show their range only. `cold` = the request paid "
        f"lazy model loading (deps >= {COLD_DEPS_MS:.0f} ms)._",
        "",
    ]
    return "\n".join(lines)


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
            cwd=BACKEND, timeout=10,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=150)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause-at", type=int, default=90)
    parser.add_argument("--resume-at", type=int, default=80)
    parser.add_argument("--no-pace", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    pool = question_pool()
    questions = pool[: args.requests]
    if len(questions) < args.requests:
        print(f"only {len(pool)} distinct questions exist; running {len(questions)}, "
              f"not repeating any (a repeat hits the prompt cache)")

    pacer = None if args.no_pace else ThermalPacer(args.pause_at, args.resume_at)
    sampler = GpuSampler()
    sampling = sampler.start()
    samples: list[dict] = []
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        for index, question in enumerate(questions, 1):
            cooldown = pacer.before_call() if pacer else 0.0
            t0 = time.monotonic()
            status, body, timings = ask(args.url, question, args.timeout)
            t1 = time.monotonic()
            sample = {
                "question": question,
                "status": status,
                "refused": body.get("refused"),
                "client_ms": round((t1 - t0) * 1000, 1),
                "timings": timings,
                "cooldown_s": round(cooldown, 1),
                "gpu": sampler.summary(t0, t1) if sampling else None,
            }
            samples.append(sample)
            gpu = sample["gpu"] or {}
            print(f"[{index}/{len(questions)}] {classify(sample):13} "
                  f"{timings.get('total', sample['client_ms']):8.0f} ms  "
                  f"{gpu.get('regime') or '-':9} {gpu.get('peak_temp_c') or '-'} C")
    except ThermalAbort as exc:
        print(f"stopped: {exc}")
    finally:
        sampler.stop()

    report = {
        "url": args.url,
        "git_sha": _git_sha(),
        "n_requests": len(samples),
        "conditions": {
            "gpu": gpu_name(),
            "pause_at": pacer.pause_at if pacer else None,
            "resume_at": pacer.resume_at if pacer else None,
            "pauses": pacer.pauses if pacer else 0,
            "total_cooldown_s": round(pacer.total_wait_s, 1) if pacer else 0.0,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "summary": summarize(samples),
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_name(args.out.name + ".json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    markdown = build_markdown(report)
    args.out.with_name(args.out.name + ".md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
