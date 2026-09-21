"""End-to-end measurement of what a user actually gets: paced, resumable, judged.

run_eval.py scores RETRIEVAL - what came back and in what order. It is the file
the published audit is built from, and it stays model-free. compare_engines.py
scores REFUSAL BEHAVIOUR per engine. This scores ANSWERS: for every case it runs
the engine the API serves, records the answer and the exact context it was
generated from, how long it took and the GPU conditions it took it under, then
judges each claim in the answer against that context.

It is built for the machine it runs on. On the reference laptop an unpaced run
reached 96 C after 33 generations and had to be stopped, and a clamped GPU is
five times slower than a cool one. So:

  generate  One LLM call per accepted case, paced by `ThermalPacer` and
            checkpointed after EVERY case to eval/e2e/raw/<suite>.<engine>.jsonl.
            A run that stops - thermal abort, crash, Ctrl-C - loses at most the
            case in flight; `--resume` continues it.
  score     Reads the raw file, judges support with an NLI model on CPU, writes
            eval/e2e/<suite>.<engine>.json and .md. `--score-only` re-scores
            existing answers, so a metric can be fixed without re-generating an
            hour of them.

The raw files hold full context text and are not committed; the reports hold
every answer, its context titles and its per-sentence support, which is enough
to audit any number in them.

Usage (the Makefile targets pin PROFILE and COLLECTION to the suite):
    PROFILE=real COLLECTION=wikipedia uv run python eval/run_e2e.py --suite serving_golden
    ... --resume        continue a run that stopped
    ... --score-only    re-score saved answers
    ... --limit 5       smoke test - NOT a measurement
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.metrics import MIN_SAMPLES_P50, MIN_SAMPLES_P95, percentile  # noqa: E402
from eval.grounding import (  # noqa: E402
    DEFAULT_NLI_MODEL,
    SUPPORT_THRESHOLD,
    check_citations,
    claim_sentences,
    sentence_support,
    summarize_answers,
)
from eval.metrics import refusal_accuracy  # noqa: E402
from eval.suites import SUITE_PROFILES, check_profile, load_suite  # noqa: E402
from eval.thermal import GpuSampler, ThermalAbort, ThermalPacer, gpu_name  # noqa: E402

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
DEFAULT_OUT = EVAL_DIR / "e2e"
MAX_ATTEMPTS = 2
RETRY_PAUSE_S = 5.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stem(suite: str, engine: str) -> str:
    return f"{suite}.{engine}"


def raw_path(out_dir: Path, suite: str, engine: str) -> Path:
    return out_dir / "raw" / f"{_stem(suite, engine)}.jsonl"


def report_paths(out_dir: Path, suite: str, engine: str) -> tuple[Path, Path]:
    # Built by name, not with_suffix(): the stem "suite.engine" already has a
    # dot, and with_suffix would replace the engine instead of appending.
    stem = _stem(suite, engine)
    return out_dir / f"{stem}.json", out_dir / f"{stem}.md"


def load_raw(path: Path) -> list[dict]:
    """Records in file order; a later record for the same QUESTION replaces an
    earlier one, so a case retried on resume is counted once, with its latest
    outcome.

    Keyed by question text, not id: the question is what was answered. Keyed by
    id, editing a suite question would have kept the answer to the old question
    under the new one - the review that found this edited fourteen.
    """
    if not path.exists():
        return []
    by_question: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            by_question[record["question"]] = record
    return list(by_question.values())


def reconcile(records: list[dict], cases: list[dict]) -> tuple[list[dict], int]:
    """Align saved records with the suite AS IT IS NOW.

    A record whose question is no longer in the suite is stale and dropped. The
    expectations (id, category, expected_*) come from the current suite, never
    from the record, so a corrected expectation applies without re-generating.
    Returns (aligned records, number dropped as stale).
    """
    by_question = {record["question"]: record for record in records}
    aligned = []
    for case in cases:
        record = by_question.pop(case["question"], None)
        if record is not None:
            aligned.append({
                **record,
                "id": case["id"],
                "category": case.get("category", "unknown"),
                "expected_refusal": bool(case.get("expected_refusal")),
                "expected_titles": case.get("expected_titles") or [],
            })
    return aligned, len(by_question)


# --- generate ----------------------------------------------------------------

def run_case(engine, case: dict, *, sampler: GpuSampler | None, cooldown_s: float,
             sleep=time.sleep) -> dict:
    """One case, retried once. An infrastructure failure is recorded as an error
    and excluded from the metrics - it is not a decision the engine made."""
    base = {
        "id": case["id"],
        "category": case.get("category", "unknown"),
        "question": case["question"],
        "expected_refusal": bool(case.get("expected_refusal")),
        "expected_titles": case.get("expected_titles") or [],
        "at": _now(),
        "cooldown_s": round(cooldown_s, 1),
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            result = engine.answer(case["question"])
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            if attempt == MAX_ATTEMPTS:
                return {**base, "error": f"{type(exc).__name__}: {exc}"[:300]}
            logger.warning("  %s attempt %s failed (%s); retrying", case["id"], attempt,
                           type(exc).__name__)
            sleep(RETRY_PAUSE_S)
            continue
        finished = time.monotonic()
        return {
            **base,
            "refused": result.refused,
            "refusal_reason": result.refusal_reason,
            "answer": result.answer,
            "citations": [citation.get("title", "") for citation in result.citations],
            "context": [
                {"title": chunk.get("title", ""), "source_id": chunk.get("source_id", ""),
                 "text": chunk.get("text", "")}
                for chunk in result.context
            ],
            "llm_calls": result.stats.get("llm_calls", 0),
            "latency_ms": round((finished - started) * 1000, 1),
            "gpu": sampler.summary(started, finished) if sampler else None,
        }
    raise AssertionError("unreachable")


def generate(engine, cases: list[dict], path: Path, *, pacer: ThermalPacer | None,
             sampler: GpuSampler | None, resume: bool) -> int:
    """Append one record per pending case. Returns how many were generated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {r["question"] for r in load_raw(path) if "error" not in r} if resume else set()
    if not resume and path.exists():
        path.unlink()
    pending = [case for case in cases if case["question"] not in done]
    logger.info("%s: %s case(s) to generate, %s kept", path.stem, len(pending),
                len(cases) - len(pending))

    with path.open("a", encoding="utf-8") as handle:
        for index, case in enumerate(pending, 1):
            cooldown = pacer.before_call() if pacer else 0.0
            record = run_case(engine, case, sampler=sampler, cooldown_s=cooldown)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            gpu = record.get("gpu") or {}
            outcome = "ERROR" if "error" in record else (
                "refused" if record["refused"] else "answered"
            )
            conditions = (
                f"{gpu.get('regime')} {gpu.get('sm_mhz')}MHz {gpu.get('peak_temp_c')}C"
                if gpu else ""
            )
            logger.info(
                "  [%s/%s] %s %s %.1fs %s", index, len(pending), case["id"], outcome,
                (record.get("latency_ms") or 0) / 1000, conditions,
            )
    return len(pending)


# --- score -------------------------------------------------------------------

def judge_records(records: list[dict], judge) -> None:
    """Attach per-sentence support to every answered record (in place)."""
    answered = [r for r in records if "error" not in r and not r["refused"]]
    for index, record in enumerate(answered, 1):
        claims = claim_sentences(record["answer"])
        contexts = [chunk["text"] for chunk in record["context"]]
        record["claims"] = claims
        record["support"] = [round(p, 4) for p in sentence_support(claims, contexts, judge)]
        if index % 10 == 0:
            logger.info("  judged %s/%s answers", index, len(answered))


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50_ms": percentile(ordered, 50) if len(ordered) >= MIN_SAMPLES_P50 else None,
        "p95_ms": percentile(ordered, 95) if len(ordered) >= MIN_SAMPLES_P95 else None,
        "min_ms": ordered[0] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
    }


def latency_summary(records: list[dict]) -> dict:
    """Latency split by whether the model ran, and by GPU regime when it did.

    p50 needs 5 samples and p95 needs 100, the same rule /metrics applies, so a
    small group reports its range instead of a percentile it cannot support.
    """
    usable = [r for r in records if "error" not in r]
    generated = [r for r in usable if r.get("llm_calls", 0) > 0]
    summary = {
        "generated": _distribution([r["latency_ms"] for r in generated]),
        "no_generation": _distribution(
            [r["latency_ms"] for r in usable if r.get("llm_calls", 0) == 0]
        ),
    }
    for regime in ("full", "throttled", "unknown"):
        group = [r for r in generated if (r.get("gpu") or {}).get("regime") == regime]
        summary[f"generated_{regime}"] = _distribution([r["latency_ms"] for r in group])
    summary["generated_no_gpu_reading"] = _distribution(
        [r["latency_ms"] for r in generated if not r.get("gpu")]
    )
    return summary


def _context_hit(record: dict) -> bool:
    wanted = {title.strip().casefold() for title in record["expected_titles"]}
    return any(chunk["title"].strip().casefold() in wanted for chunk in record["context"])


def score(records: list[dict], suite_cases: list[dict]) -> dict:
    usable = [r for r in records if "error" not in r]
    answerable = [r for r in usable if not r["expected_refusal"]]
    unanswerable = [r for r in usable if r["expected_refusal"]]
    soft_refusals = [r for r in answerable if r["refusal_reason"] == "soft_refusal"]

    metrics = {
        "n_cases": len(suite_cases),
        "n_scored": len(usable),
        "n_errors": len(records) - len(usable),
        "n_missing": len({c["id"] for c in suite_cases} - {r["id"] for r in records}),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "refusal_accuracy": refusal_accuracy([r["refused"] for r in unanswerable]),
        "false_accept_rate": (
            sum(not r["refused"] for r in unanswerable) / len(unanswerable) if unanswerable
            else None
        ),
        "answerable_refusal_rate": (
            sum(r["refused"] for r in answerable) / len(answerable) if answerable else None
        ),
        # Of the answerable questions, how often was the right article in front
        # of the model - and how often did the model decline it anyway.
        "context_hit_rate": (
            sum(_context_hit(r) for r in answerable) / len(answerable) if answerable else None
        ),
        "soft_refusals_answerable": len(soft_refusals),
        "soft_refusals_with_article_in_context": sum(_context_hit(r) for r in soft_refusals),
    }
    metrics["complete"] = metrics["n_errors"] == 0 and metrics["n_missing"] == 0

    answers = summarize_answers(
        [
            {
                "answered": not r["refused"],
                "answer": r["answer"],
                "context": [chunk["text"] for chunk in r["context"]],
                "support": r.get("support"),
            }
            for r in usable
        ]
    )
    return {"metrics": metrics, "answers": answers, "latency": latency_summary(records)}


def compact_case(record: dict) -> dict:
    if "error" in record:
        return {"id": record["id"], "question": record["question"], "error": record["error"]}
    check = check_citations(record["answer"], len(record["context"]))
    row = {
        "id": record["id"],
        "question": record["question"],
        "expected_refusal": record["expected_refusal"],
        "refused": record["refused"],
        "refusal_reason": record["refusal_reason"],
        "answer": record["answer"],
        "context_titles": [chunk["title"] for chunk in record["context"]],
        "latency_ms": record["latency_ms"],
        "gpu_regime": (record.get("gpu") or {}).get("regime"),
    }
    if not record["expected_refusal"]:
        row["context_hit"] = _context_hit(record)
    if not record["refused"]:
        row.update({"citation_valid": check.valid, "placeholder_citation": check.placeholder})
        if "support" in record:
            row["claims"] = [
                {"sentence": s, "support": p} for s, p in zip(record["claims"], record["support"])
            ]
    return row


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def build_markdown(report: dict) -> str:
    m, a, lat = report["metrics"], report["answers"], report["latency"]
    prov, cond = report["provenance"], report["conditions"]
    lines = [
        f"## End-to-end: `{report['suite']}` / `{report['engine']}`",
        "",
        f"_corpus `{prov.get('collection')}` ({prov.get('vector_count')} vectors, profile "
        f"`{prov.get('profile')}`) | model `{prov.get('llm_model')}` | judge "
        f"`{report.get('nli_model') or 'not run'}` | git `{prov.get('git_sha')}`_",
        "",
        "| refusal | value | | answers | value |",
        "|---|---:|---|---|---:|",
        f"| false_accept_rate | {_fmt(m['false_accept_rate'])} | | answered | {a['n_answered']} |",
        f"| refusal_accuracy | {_fmt(m['refusal_accuracy'])} | | supported_sentence_rate | "
        f"{_fmt(a.get('supported_sentence_rate'))} |",
        f"| answerable_refusal_rate | {_fmt(m['answerable_refusal_rate'])} | | "
        f"fully_supported_answer_rate | {_fmt(a.get('fully_supported_answer_rate'))} |",
        f"| context_hit_rate | {_fmt(m['context_hit_rate'])} | | citation_valid_rate | "
        f"{_fmt(a['citation_valid_rate'])} |",
        f"| soft refusals (article in context) | {m['soft_refusals_answerable']} "
        f"({m['soft_refusals_with_article_in_context']}) | | placeholder_citation_rate | "
        f"{_fmt(a['placeholder_citation_rate'])} |",
        "",
        "| latency (ms) | n | p50 | p95 | min | max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("generated", "generated_full", "generated_throttled", "generated_unknown",
                 "no_generation"):
        d = lat[name]
        lines.append(
            f"| {name} | {d['n']} | {_fmt(d['p50_ms'], 0)} | {_fmt(d['p95_ms'], 0)} | "
            f"{_fmt(d['min_ms'], 0)} | {_fmt(d['max_ms'], 0)} |"
        )
    lines += [
        "",
        f"_GPU: {cond.get('gpu') or 'no reading'}; paced at {cond.get('pause_at')}/"
        f"{cond.get('resume_at')} C; cooled {cond.get('total_cooldown_s')} s in "
        f"{cond.get('pauses')} pause(s). p50 needs {MIN_SAMPLES_P50} samples, p95 "
        f"{MIN_SAMPLES_P95}. A claim is supported at entailment >= "
        f"{a.get('support_threshold', SUPPORT_THRESHOLD)}._",
        "",
    ]
    if not m["complete"]:
        lines.insert(
            1,
            f"\n> **INCOMPLETE - not a measurement.** {m['n_errors']} error(s), "
            f"{m['n_missing']} case(s) never run. Finish with --resume.\n",
        )
    unsupported = [
        (case["id"], claim["sentence"], claim["support"])
        for case in report["cases"]
        for claim in case.get("claims", [])
        if claim["support"] < a.get("support_threshold", SUPPORT_THRESHOLD)
    ]
    if unsupported:
        lines += ["### Unsupported claims", ""]
        lines += [f"- `{cid}` ({p:.2f}): {sentence}" for cid, sentence, p in unsupported]
        lines.append("")
    return "\n".join(lines)


def generate_suite(suite: str, engine, args, *, provenance: dict,
                   pacer: ThermalPacer | None, sampler: GpuSampler | None) -> bool:
    """Generate one suite and record its conditions beside it. False on a
    thermal abort - everything generated so far is already on disk."""
    cases = load_suite(suite)
    if args.limit:
        cases = cases[: args.limit]
    path = raw_path(args.out_dir, suite, args.engine)
    meta_path = path.with_suffix(".meta.json")
    meta = {}
    if args.resume and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("started_at", _now())
    meta.update({
        "provenance": provenance,
        "gpu": gpu_name(),
        "pause_at": pacer.pause_at if pacer else None,
        "resume_at": pacer.resume_at if pacer else None,
        "limit": args.limit,
    })
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # The pacer is shared across suites; record this suite's share, not the total.
    pauses_before = pacer.pauses if pacer else 0
    waited_before = pacer.total_wait_s if pacer else 0.0
    finished = True
    try:
        generate(engine, cases, path, pacer=pacer, sampler=sampler, resume=args.resume)
    except ThermalAbort as exc:
        logger.error("%s - stopped. Re-run with --resume once the GPU has cooled.", exc)
        finished = False
    meta["finished_at"] = _now()
    if pacer:
        meta["pauses"] = (meta.get("pauses") or 0) + pacer.pauses - pauses_before
        meta["total_cooldown_s"] = round(
            (meta.get("total_cooldown_s") or 0) + pacer.total_wait_s - waited_before, 1
        )
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return finished


# --- main --------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", action="append", required=True, choices=sorted(SUITE_PROFILES))
    parser.add_argument("--engine", default="direct")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--no-nli", action="store_true", help="skip the support judge")
    parser.add_argument("--nli-model", default=DEFAULT_NLI_MODEL)
    parser.add_argument("--nli-device", default="cpu")
    parser.add_argument("--pause-at", type=int, default=85)
    parser.add_argument("--resume-at", type=int, default=75)
    parser.add_argument("--no-pace", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="first N cases only: a smoke test, NOT a measurement")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)

    from app.core.config import settings
    from app.core.quality import build_provenance
    from app.core.vectorstore import QdrantStore

    for suite in args.suite:
        check_profile(suite, settings.profile)

    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    exit_code = 0

    if not args.score_only:
        from app.core.embeddings import BGEEmbedder
        from app.core.llm import OllamaLLM
        from engines import make_engine

        embedder = BGEEmbedder(model_name=settings.embed_model)
        llm = OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)
        engine = make_engine(embedder, store, llm, choice=args.engine)
        pacer = None if args.no_pace else ThermalPacer(args.pause_at, args.resume_at)
        sampler = GpuSampler()
        sampling = sampler.start()
        provenance = {
            **build_provenance(store=store, top_k=settings.top_k),
            "llm_model": settings.llm_model,
            "engine": args.engine,
        }
        try:
            for suite in args.suite:
                finished = generate_suite(
                    suite, engine, args, provenance=provenance, pacer=pacer,
                    sampler=sampler if sampling else None,
                )
                if not finished:
                    exit_code = 2
                    break
        finally:
            sampler.stop()

    judge = None
    if not args.no_nli:
        from eval.grounding import NLIJudge

        judge = NLIJudge(args.nli_model, device=args.nli_device)

    for suite in args.suite:
        path = raw_path(args.out_dir, suite, args.engine)
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        cases = load_suite(suite)
        if meta.get("limit"):
            cases = cases[: meta["limit"]]
        records, stale = reconcile(load_raw(path), cases)
        if stale:
            logger.warning("%s: %s saved answer(s) are for questions no longer in the suite "
                           "- ignored", suite, stale)
        if not records:
            logger.warning("%s: nothing generated yet - skipping", suite)
            continue
        if judge is not None:
            judge_records(records, judge)
        report = {
            "suite": suite,
            "engine": args.engine,
            "nli_model": judge.model_name if judge else None,
            "provenance": meta.get("provenance", {}),
            "conditions": {key: meta.get(key) for key in
                           ("gpu", "pause_at", "resume_at", "pauses", "total_cooldown_s",
                            "started_at", "finished_at", "limit")},
            **score(records, cases),
            "cases": [compact_case(record) for record in records],
        }
        json_path, md_path = report_paths(args.out_dir, suite, args.engine)
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        markdown = build_markdown(report)
        md_path.write_text(markdown, encoding="utf-8")
        print(markdown)
        if meta.get("limit"):
            print("NOTE: --limit was set, so this is a smoke test and not a measurement.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
