from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

RECALL_GATE = 0.8
REFUSAL_GATE = 0.8
PRECISION_GATE = 0.6
FALSE_ACCEPT_MAX = 0.1
MIN_CASES = 20
MIN_UNANSWERABLE = 5


def validate_golden_set(golden: list[dict]) -> tuple[list[dict], list[dict]]:
    answerable = [item for item in golden if not item.get("expected_refusal")]
    unanswerable = [item for item in golden if item.get("expected_refusal")]

    # Reject an incomplete golden set up front, otherwise a missing subset would
    # silently skip its gate and the eval would "pass" without measuring it.
    if len(golden) < MIN_CASES or len(unanswerable) < MIN_UNANSWERABLE or not answerable:
        raise SystemExit(
            f"golden.jsonl must contain >= {MIN_CASES} cases, "
            f">= {MIN_UNANSWERABLE} unanswerable, and >= 1 answerable "
            f"(got {len(golden)} total, {len(unanswerable)} unanswerable, "
            f"{len(answerable)} answerable)"
        )

    return answerable, unanswerable


def _expected_terms(item: dict) -> list[str]:
    return item.get("expected_terms") or item.get("expected_sources") or []


def _expected_titles(item: dict) -> list[str]:
    return item.get("expected_titles") or []


def _title_key(title: str) -> str:
    """One normalization rule for every title comparison.

    Recall matched titles casefolded while precision compared them raw, so a
    corpus title whose case differed from golden.jsonl scored recall 1.0 and
    precision 0.0 for the same case — and the precision gate failed on a
    retrieval that had actually succeeded.
    """
    return title.strip().casefold()


def _match_expected_titles(chunks: list[dict], expected_titles: list[str]) -> list[str]:
    retrieved_titles = {_title_key(chunk.get("title", "")) for chunk in chunks}
    return [
        expected for expected in expected_titles if _title_key(expected) in retrieved_titles
    ]


def score_answerable_case(item: dict, chunks: list[dict], refused: bool, *, k: int) -> dict:
    from eval.metrics import recall_at_k, reciprocal_rank

    texts = [chunk["text"] for chunk in chunks]
    combined = " ".join(texts).lower()
    expected_terms = _expected_terms(item)
    expected_titles = _expected_titles(item)
    matched_terms = [keyword for keyword in expected_terms if keyword.lower() in combined]
    missing_terms = [keyword for keyword in expected_terms if keyword.lower() not in combined]
    matched_titles = _match_expected_titles(chunks, expected_titles)
    missing_titles = [title for title in expected_titles if title not in matched_titles]

    if refused:
        recall_score = 0.0
        rr_score = 0.0
        precision_score = 0.0
    elif expected_titles:
        recall_score = len(matched_titles) / len(expected_titles)
        rr_score = reciprocal_rank([chunk.get("title", "") for chunk in chunks], expected_titles)
        matched_keys = {_title_key(title) for title in matched_titles}
        relevant = sum(
            1 for chunk in chunks[:k] if _title_key(chunk.get("title", "")) in matched_keys
        )
        precision_score = relevant / k if k else 0.0
    else:
        recall_score = recall_at_k(texts, expected_terms, k=k)
        rr_score = reciprocal_rank(texts, expected_terms)
        relevant = sum(
            1
            for text in texts[:k]
            if any(keyword.lower() in text.lower() for keyword in expected_terms)
        )
        precision_score = relevant / k if k else 0.0

    return {
        "recall": recall_score,
        "reciprocal_rank": rr_score,
        "precision": precision_score,
        "matched_terms": matched_terms,
        "missing_terms": missing_terms,
        "matched_titles": matched_titles,
        "missing_titles": missing_titles,
    }


def evaluate_golden(golden: list[dict], embedder, store, llm=None, *, k: int) -> dict:
    """Score the golden set. Retrieval-only unless *llm* is supplied.

    Passing an ``llm`` enables the groundedness metric, which needs a generated
    answer per answerable case — one LLM call each. That turns a seconds-long
    retrieval-only run into a minutes-long one and adds a hard dependency on the
    LLM being reachable, so it is opt-in (``--with-groundedness``).

    When groundedness is not measured the key is OMITTED from the report rather
    than reported as 0.0 — "not measured" must not look like "badly grounded".
    """
    from app.core.refusal import decide_evidence, is_refusal
    from app.core.retrieval import retrieve
    from eval.metrics import mrr, refusal_accuracy

    answerable, unanswerable = validate_golden_set(golden)
    measure_generation = llm is not None
    if measure_generation:
        from app.core.prompt import build_prompt
        from eval.metrics import groundedness

    recall_scores: list[float] = []
    reciprocal_rank_scores: list[float] = []
    groundedness_scores: list[float] = []
    e2e_answerable_refused: list[bool] = []
    e2e_unanswerable_refused: list[bool] = []
    cases: list[dict] = []

    for item in answerable:
        question = item["question"]
        chunks, refused = retrieve(question, embedder, store, top_k=k)
        texts = [chunk["text"] for chunk in chunks]
        evidence = decide_evidence(question, chunks)
        score = score_answerable_case(item, chunks, refused, k=k)

        recall_score = score["recall"]
        rr_score = score["reciprocal_rank"]
        recall_scores.append(recall_score)
        reciprocal_rank_scores.append(rr_score)
        case_report = {
            "id": item.get("id", question),
            "category": item.get("category", "unknown"),
            "question": question,
            "expected_refusal": False,
            "refused": refused,
            "recall": recall_score,
            "reciprocal_rank": rr_score,
            "precision": score["precision"],
            "matched_sources": score["matched_terms"],
            "missing_sources": score["missing_terms"],
            "matched_titles": score["matched_titles"],
            "missing_titles": score["missing_titles"],
            "evidence_reason": evidence.reason,
            "top_score": evidence.top_score,
            "score_margin": evidence.score_margin,
            "overlap_terms": evidence.overlap_terms,
            "retrieved": [
                {
                    "rank": index + 1,
                    "score": chunk.get("score", 0.0),
                    "title": chunk.get("title", ""),
                    "source_id": chunk.get("source_id", ""),
                    "excerpt": chunk.get("text", "")[:240],
                }
                for index, chunk in enumerate(chunks)
            ],
        }

        if measure_generation:
            answer = llm.generate(build_prompt(question, chunks)) if chunks else ""
            groundedness_score = groundedness(answer, texts)
            groundedness_scores.append(groundedness_score)
            case_report["groundedness"] = groundedness_score

            # Three layers, kept separate on purpose. `refused` above is the
            # RETRIEVAL decision; the model can still decline evidence that
            # retrieval accepted, and the combination is what the user sees.
            answer_refused = is_refusal(answer)
            case_report["answer_refused"] = answer_refused
            case_report["end_to_end_refused"] = bool(refused or answer_refused)
            e2e_answerable_refused.append(case_report["end_to_end_refused"])
            logger.info(
                "Q: %r | recall@%s=%.2f | RR=%.2f | groundedness=%.2f",
                question[:50],
                k,
                recall_score,
                rr_score,
                groundedness_score,
            )
        else:
            logger.info(
                "Q: %r | recall@%s=%.2f | RR=%.2f", question[:50], k, recall_score, rr_score
            )
        cases.append(case_report)

    refused_flags: list[bool] = []
    for item in unanswerable:
        question = item["question"]
        chunks, refused = retrieve(question, embedder, store, top_k=k)
        evidence = decide_evidence(question, chunks)
        refused_flags.append(refused)

        # Only worth generating when retrieval ACCEPTED an unanswerable question:
        # that is the false accept the model still has a chance to catch. When
        # retrieval already refused, the API short-circuits and never calls the
        # LLM, so end-to-end is a refusal without spending a generation.
        e2e_refused = refused
        answer_refused = None
        if measure_generation and not refused:
            answer = llm.generate(build_prompt(question, chunks)) if chunks else ""
            answer_refused = is_refusal(answer)
            e2e_refused = bool(answer_refused)
        if measure_generation:
            e2e_unanswerable_refused.append(bool(e2e_refused))
        logger.info("Q: %r | refused=%s", question[:50], refused)
        cases.append(
            {
                "id": item.get("id", question),
                "category": item.get("category", "unknown"),
                "question": question,
                "expected_refusal": True,
                "refused": refused,
                "correct": refused is True,
                **(
                    {
                        "answer_refused": answer_refused,
                        "end_to_end_refused": bool(e2e_refused),
                    }
                    if measure_generation
                    else {}
                ),
                "evidence_reason": evidence.reason,
                "top_score": evidence.top_score,
                "score_margin": evidence.score_margin,
                "overlap_terms": evidence.overlap_terms,
                "retrieved": [
                    {
                        "rank": index + 1,
                        "score": chunk.get("score", 0.0),
                        "title": chunk.get("title", ""),
                        "source_id": chunk.get("source_id", ""),
                        "excerpt": chunk.get("text", "")[:240],
                    }
                    for index, chunk in enumerate(chunks)
                ],
            }
        )

    report = {
        f"recall@{k}": sum(recall_scores) / len(answerable) if answerable else 0.0,
        "mrr": mrr(reciprocal_rank_scores),
        f"precision@{k}": (
            sum(case["precision"] for case in cases if not case["expected_refusal"])
            / len(answerable)
            if answerable
            else 0.0
        ),
        "refusal_accuracy": refusal_accuracy(refused_flags),
        "answerable_refusal_rate": (
            sum(1 for case in cases if not case["expected_refusal"] and case["refused"])
            / len(answerable)
            if answerable
            else 0.0
        ),
        "false_accept_rate": (
            sum(1 for case in cases if case["expected_refusal"] and not case["refused"])
            / len(unanswerable)
            if unanswerable
            else 0.0
        ),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "cases": cases,
    }
    if measure_generation:
        report["groundedness"] = (
            sum(groundedness_scores) / len(answerable) if answerable else 0.0
        )
        # END-TO-END counterparts, reported ALONGSIDE the retrieval metrics and
        # never replacing them: the pair is what reveals whether the model is
        # catching what retrieval let through, or refusing what it accepted.
        n_unans = len(e2e_unanswerable_refused)
        n_ans = len(e2e_answerable_refused)
        report["e2e_refusal_accuracy"] = (
            sum(1 for flag in e2e_unanswerable_refused if flag) / n_unans if n_unans else 0.0
        )
        report["e2e_answerable_refusal_rate"] = (
            sum(1 for flag in e2e_answerable_refused if flag) / n_ans if n_ans else 0.0
        )
        report["e2e_false_accept_rate"] = (
            sum(1 for flag in e2e_unanswerable_refused if not flag) / n_unans
            if n_unans
            else 0.0
        )
    return report


def write_markdown_report(report: dict, path: Path, *, k: int) -> None:
    lines = [
        "# Evaluation Report",
        "",
        "## Summary",
        "",
        f"- recall@{k}: {report[f'recall@{k}']:.4f}",
        f"- mrr: {report['mrr']:.4f}",
        f"- precision@{k}: {report[f'precision@{k}']:.4f}",
        f"- refusal_accuracy: {report['refusal_accuracy']:.4f}",
        f"- answerable_refusal_rate: {report['answerable_refusal_rate']:.4f}",
        f"- false_accept_rate: {report['false_accept_rate']:.4f}",
        f"- n_answerable: {report['n_answerable']}",
        f"- n_unanswerable: {report['n_unanswerable']}",
    ]
    if "groundedness" in report:
        lines.append(f"- groundedness: {report['groundedness']:.4f}")

    lines.extend(["", "## Cases", ""])
    for case in report["cases"]:
        status = "PASS"
        if case.get("expected_refusal"):
            status = "PASS" if case["refused"] else "FAIL"
        elif case.get("missing_sources"):
            status = "FAIL"
        elif case.get("missing_titles"):
            status = "FAIL"
        lines.extend(
            [
                f"### {status}: {case['question']}",
                "",
                f"- expected_refusal: {case['expected_refusal']}",
                f"- refused: {case['refused']}",
                f"- evidence_reason: {case['evidence_reason']}",
                f"- top_score: {case['top_score']:.4f}",
                f"- score_margin: {case['score_margin']:.4f}",
                f"- overlap_terms: {', '.join(case['overlap_terms']) or '(none)'}",
            ]
        )
        if "recall" in case:
            lines.extend(
                [
                    f"- recall: {case['recall']:.4f}",
                    f"- reciprocal_rank: {case['reciprocal_rank']:.4f}",
                    f"- matched_sources: {', '.join(case['matched_sources']) or '(none)'}",
                    f"- missing_sources: {', '.join(case['missing_sources']) or '(none)'}",
                    f"- matched_titles: {', '.join(case.get('matched_titles', [])) or '(none)'}",
                    f"- missing_titles: {', '.join(case.get('missing_titles', [])) or '(none)'}",
                ]
            )
        if case["retrieved"]:
            top_titles = ", ".join(item["title"] for item in case["retrieved"][:3])
            lines.append(f"- top_titles: {top_titles}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def gate_failures(report: dict, k: int) -> list[str]:
    failures = []
    if report[f"recall@{k}"] < RECALL_GATE:
        failures.append(f"recall@{k}={report[f'recall@{k}']:.2f} < {RECALL_GATE}")
    if report[f"precision@{k}"] < PRECISION_GATE:
        failures.append(f"precision@{k}={report[f'precision@{k}']:.2f} < {PRECISION_GATE}")
    if report["refusal_accuracy"] < REFUSAL_GATE:
        failures.append(f"refusal_accuracy={report['refusal_accuracy']:.2f} < {REFUSAL_GATE}")
    if report["false_accept_rate"] > FALSE_ACCEPT_MAX:
        failures.append(f"false_accept_rate={report['false_accept_rate']:.2f} > {FALSE_ACCEPT_MAX}")
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval quality and refusal behaviour against golden.jsonl."
    )
    parser.add_argument(
        "--with-groundedness",
        action="store_true",
        help=(
            "Also measure groundedness. Generates one answer per answerable case, so "
            "this requires the LLM to be running and takes minutes instead of seconds. "
            "Off by default to keep the fast, retrieval-only path."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.vectorstore import QdrantStore

    args = parse_args(argv)

    golden_path = Path(__file__).parent / "golden.jsonl"
    golden = [json.loads(line) for line in golden_path.read_text().splitlines() if line.strip()]

    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)

    llm = None
    if args.with_groundedness:
        # Only touched on the opt-in path: no LLM import, no connection, no cost
        # on the default run.
        from app.core.llm import OllamaLLM

        llm = OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)
        logger.info("Groundedness enabled: generating one answer per answerable case.")

    k = settings.top_k
    report = evaluate_golden(golden, embedder, store, llm, k=k)

    print("\n=== Evaluation Report ===")
    for key, value in report.items():
        if key == "cases":
            continue
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")
    if not args.with_groundedness:
        print("  groundedness: not measured (re-run with --with-groundedness)")

    report_path = Path(__file__).parent / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", report_path)
    markdown_path = Path(__file__).parent / "report.md"
    write_markdown_report(report, markdown_path, k=k)
    logger.info("Markdown report written to %s", markdown_path)

    # Gates are unconditional: the golden-set validation above guarantees both
    # subsets are non-empty, so neither metric can be skipped. Groundedness is
    # reported only and never gates.
    failures = gate_failures(report, k)

    if failures:
        print(f"\nGATE FAILED: {'; '.join(failures)}")
        sys.exit(1)
    print("\nGATE PASSED")


if __name__ == "__main__":
    main()
