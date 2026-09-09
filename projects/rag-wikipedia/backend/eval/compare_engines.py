"""Score two engines on the same suites, and report the difference honestly.

A framework is only worth its dependency if it beats not using one. This runs
each registered engine over the committed suites and reports what it did and
what it cost, so `langgraph` can be compared against `direct` on the numbers
rather than on enthusiasm.

WHAT THIS MEASURES, AND WHAT IT DOES NOT. run_eval.py scores RETRIEVAL - what
came back and in what order - and it is the file the published audit is built
from. This scores BEHAVIOUR: given the question, did the engine answer or
decline, and what did that cost in model calls. The two ask different questions,
which is why this is a separate script rather than a flag on that one. The other
reason is practical: run_eval.py is watched by audit_freshness, so editing it
would stale the committed measurement for a change that does not affect it.

The refusal metrics use the same definitions as run_eval.py, imported rather
than reimplemented, because a comparison against a differently-computed baseline
is not a comparison.

    refusal_accuracy         of the UNANSWERABLE cases, the fraction refused
    false_accept_rate        of the UNANSWERABLE cases, the fraction answered
    answerable_refusal_rate  of the ANSWERABLE cases, the fraction refused

false_accept_rate is the number that is red (0.600 on holdout against a 0.10
gate) and the one the graph engine is trying to move. answerable_refusal_rate is
the price: an engine can drive false accepts to zero by refusing everything, and
these two must always be read together.

Usage:
    uv run python eval/compare_engines.py --suite holdout --suite adversarial
    uv run python eval/compare_engines.py --engines direct --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from app.core.config import settings
from app.core.embeddings import BGEEmbedder
from app.core.llm import OllamaLLM
from app.core.vectorstore import QdrantStore
from engines import ENGINE_REGISTRY, make_engine
from eval.metrics import refusal_accuracy

logger = logging.getLogger(__name__)

SUITES = ("golden", "holdout", "adversarial")
EVAL_DIR = Path(__file__).parent


def load_suite(name: str) -> list[dict]:
    path = EVAL_DIR / f"{name}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def score_case(engine, case: dict) -> dict:
    """Run one case and record what happened, including what it cost."""
    started = time.perf_counter()
    result = engine.answer(case["question"])
    elapsed_ms = (time.perf_counter() - started) * 1000

    expected_refusal = bool(case.get("expected_refusal"))
    return {
        "id": case.get("id"),
        "question": case["question"],
        "expected_refusal": expected_refusal,
        "refused": result.refused,
        # The interesting cases: refused when it should have answered, or
        # answered when it should have refused.
        "correct": result.refused == expected_refusal,
        "refusal_reason": result.refusal_reason,
        "llm_calls": result.stats.get("llm_calls", 0),
        "rewrites": result.stats.get("rewrites", 0),
        "latency_ms": round(elapsed_ms, 1),
    }


def score_suite(engine, cases: list[dict]) -> dict:
    scored = [score_case(engine, case) for case in cases]
    unanswerable = [case for case in scored if case["expected_refusal"]]
    answerable = [case for case in scored if not case["expected_refusal"]]

    return {
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "refusal_accuracy": refusal_accuracy([case["refused"] for case in unanswerable]),
        "false_accept_rate": (
            sum(1 for case in unanswerable if not case["refused"]) / len(unanswerable)
            if unanswerable
            else 0.0
        ),
        "answerable_refusal_rate": (
            sum(1 for case in answerable if case["refused"]) / len(answerable)
            if answerable
            else 0.0
        ),
        "mean_llm_calls": (
            sum(case["llm_calls"] for case in scored) / len(scored) if scored else 0.0
        ),
        "mean_latency_ms": (
            sum(case["latency_ms"] for case in scored) / len(scored) if scored else 0.0
        ),
        "cases": scored,
    }


METRICS = (
    ("false_accept_rate", "lower is better"),
    ("refusal_accuracy", "higher is better"),
    ("answerable_refusal_rate", "the price of the above"),
    ("mean_llm_calls", "cost"),
    ("mean_latency_ms", "cost"),
)


def build_markdown(report: dict) -> str:
    engines = list(report["engines"])
    lines = [
        "## Engine comparison",
        "",
        f"_corpus: {report['collection']} | model: {report['llm_model']} | "
        f"embedder: {report['embed_model']}_",
        "",
    ]
    for suite in report["suites"]:
        lines += [
            f"### `{suite}`",
            "",
            "| metric | " + " | ".join(f"`{name}`" for name in engines) + " | delta | |",
            "|---|" + "---:|" * (len(engines) + 1) + "---|",
        ]
        for metric, note in METRICS:
            values = [report["engines"][name][suite][metric] for name in engines]
            cells = " | ".join(f"{value:.3f}" for value in values)
            delta = values[-1] - values[0] if len(values) > 1 else 0.0
            lines.append(f"| `{metric}` | {cells} | {delta:+.3f} | {note} |")
        lines.append("")
    lines += [
        "_false_accept_rate and answerable_refusal_rate must be read together: "
        "any engine can drive the first to zero by refusing everything._",
        "",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", action="append", choices=SUITES, default=None,
        help="repeatable; defaults to every suite",
    )
    parser.add_argument(
        "--engines", default=",".join(ENGINE_REGISTRY),
        help=f"comma-separated; available: {','.join(sorted(ENGINE_REGISTRY))}",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="score only the first N cases of each suite. For checking the "
             "harness quickly - a truncated suite is NOT a measurement.",
    )
    parser.add_argument("--out", default=str(EVAL_DIR / "engine_comparison"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    suites = args.suite or list(SUITES)
    names = [name.strip() for name in args.engines.split(",") if name.strip()]

    # Built once and SHARED by every engine, so a difference in the numbers is a
    # difference in the engine rather than in what it was given.
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    llm = OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)

    report: dict = {
        "collection": settings.collection,
        "llm_model": settings.llm_model,
        "embed_model": settings.embed_model,
        "limit": args.limit,
        "suites": suites,
        "engines": {},
    }

    for name in names:
        engine = make_engine(embedder, store, llm, choice=name)
        report["engines"][name] = {}
        for suite in suites:
            cases = load_suite(suite)
            if args.limit:
                cases = cases[: args.limit]
            logger.info("[%s] %s: %s cases", name, suite, len(cases))
            report["engines"][name][suite] = score_suite(engine, cases)
            scored = report["engines"][name][suite]
            logger.info(
                "[%s] %s: false_accept=%.3f refusal_acc=%.3f llm_calls=%.2f",
                name, suite, scored["false_accept_rate"],
                scored["refusal_accuracy"], scored["mean_llm_calls"],
            )

    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = build_markdown(report)
    out.with_suffix(".md").write_text(markdown, encoding="utf-8")
    print(markdown)
    if args.limit:
        print("NOTE: --limit was set, so this is a smoke test and not a measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
