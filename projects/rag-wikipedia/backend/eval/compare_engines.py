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


# A local model runner is not reliable infrastructure. Measured on this
# machine: a run died 25 minutes in with "model runner has unexpectedly
# stopped, this may be due to resource limitations", and the server was healthy
# again seconds later. The same reasoning as MAX_STREAM_RESTARTS in
# pipeline/sources.py - a transient failure must not destroy an hour of work.
MAX_ATTEMPTS = 2
RETRY_PAUSE_S = 5.0


def score_case(engine, case: dict) -> dict:
    """Run one case and record what happened, including what it cost.

    A case that fails after its retries is recorded as an ERROR and excluded
    from the metrics rather than counted as a refusal. An infrastructure
    failure is not a decision the engine made, and scoring it as one would
    quietly credit an engine for a crash.
    """
    expected_refusal = bool(case.get("expected_refusal"))
    base = {
        "id": case.get("id"),
        "question": case["question"],
        "expected_refusal": expected_refusal,
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            result = engine.answer(case["question"])
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            if attempt == MAX_ATTEMPTS:
                logger.warning("  case %s failed after %s attempts: %s",
                               case.get("id"), MAX_ATTEMPTS, exc)
                return {**base, "error": f"{type(exc).__name__}: {exc}"[:300]}
            logger.warning("  case %s attempt %s failed (%s); retrying",
                           case.get("id"), attempt, type(exc).__name__)
            time.sleep(RETRY_PAUSE_S)
            continue

        return {
            **base,
            "refused": result.refused,
            # The interesting cases: refused when it should have answered, or
            # answered when it should have refused.
            "correct": result.refused == expected_refusal,
            "refusal_reason": result.refusal_reason,
            "llm_calls": result.stats.get("llm_calls", 0),
            "rewrites": result.stats.get("rewrites", 0),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    raise AssertionError("unreachable")


def score_suite(engine, cases: list[dict]) -> dict:
    scored = [score_case(engine, case) for case in cases]
    # Errored cases are excluded from every metric and counted separately. A
    # suite with errors is NOT a clean measurement, and says so in the report
    # rather than quietly reporting a rate over a smaller denominator.
    usable = [case for case in scored if not case.get("error")]
    unanswerable = [case for case in usable if case["expected_refusal"]]
    answerable = [case for case in usable if not case["expected_refusal"]]

    return {
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "n_errors": len(scored) - len(usable),
        "complete": len(usable) == len(scored),
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
            sum(case["llm_calls"] for case in usable) / len(usable) if usable else 0.0
        ),
        "mean_latency_ms": (
            sum(case["latency_ms"] for case in usable) / len(usable) if usable else 0.0
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
    # Incompleteness is stated at the TOP of the report, never buried in the
    # JSON. A rate computed over the cases that happened to survive is not the
    # rate, and a reader who skims the table must not be able to miss that.
    incomplete = [
        f"`{name}`/`{suite}`: {report['engines'][name][suite]['n_errors']} case(s) errored"
        for name in engines
        for suite in report["suites"]
        if not report["engines"][name][suite]["complete"]
    ]
    if incomplete:
        lines.insert(
            1,
            "\n> **INCOMPLETE - not a measurement.** Cases failed and were excluded: "
            + "; ".join(incomplete)
            + ". Re-run before reading these numbers as a result.\n",
        )
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
            # Saved after every pair. A model runner that dies an hour in must
            # not take the measurement with it.
            Path(args.out).with_suffix(".json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            if scored["n_errors"]:
                logger.warning(
                    "[%s] %s: %s case(s) errored and are excluded",
                    name, suite, scored["n_errors"],
                )
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
