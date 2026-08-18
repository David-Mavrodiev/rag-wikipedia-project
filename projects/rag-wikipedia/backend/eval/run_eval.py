from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

RECALL_GATE = 0.8
REFUSAL_GATE = 0.8
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


def evaluate_golden(golden: list[dict], embedder, store, llm, k: int) -> dict:
    from app.core.prompt import build_prompt
    from app.core.retrieval import retrieve
    from eval.metrics import groundedness, mrr, recall_at_k, reciprocal_rank, refusal_accuracy

    answerable, unanswerable = validate_golden_set(golden)

    recall_scores: list[float] = []
    reciprocal_rank_scores: list[float] = []
    groundedness_scores: list[float] = []

    for item in answerable:
        question = item["question"]
        expected = item["expected_sources"]
        chunks, _ = retrieve(question, embedder, store, top_k=k)
        texts = [chunk["text"] for chunk in chunks]

        recall_score = recall_at_k(texts, expected, k=k)
        rr_score = reciprocal_rank(texts, expected)
        answer = llm.generate(build_prompt(question, chunks)) if chunks else ""
        groundedness_score = groundedness(answer, texts)

        recall_scores.append(recall_score)
        reciprocal_rank_scores.append(rr_score)
        groundedness_scores.append(groundedness_score)

        logger.info(
            "Q: %r | recall@%s=%.2f | RR=%.2f | groundedness=%.2f",
            question[:50],
            k,
            recall_score,
            rr_score,
            groundedness_score,
        )

    refused_flags: list[bool] = []
    for item in unanswerable:
        question = item["question"]
        _, refused = retrieve(question, embedder, store, top_k=k)
        refused_flags.append(refused)
        logger.info("Q: %r | refused=%s", question[:50], refused)

    return {
        f"recall@{k}": sum(recall_scores) / len(answerable) if answerable else 0.0,
        "mrr": mrr(reciprocal_rank_scores),
        "groundedness": sum(groundedness_scores) / len(answerable) if answerable else 0.0,
        "refusal_accuracy": refusal_accuracy(refused_flags),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
    }


def gate_failures(report: dict, k: int) -> list[str]:
    failures = []
    if report[f"recall@{k}"] < RECALL_GATE:
        failures.append(f"recall@{k}={report[f'recall@{k}']:.2f} < {RECALL_GATE}")
    if report["refusal_accuracy"] < REFUSAL_GATE:
        failures.append(f"refusal_accuracy={report['refusal_accuracy']:.2f} < {REFUSAL_GATE}")
    return failures


def main() -> None:
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.llm import OllamaLLM
    from app.core.vectorstore import QdrantStore

    golden_path = Path(__file__).parent / "golden.jsonl"
    golden = [json.loads(line) for line in golden_path.read_text().splitlines() if line.strip()]

    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    llm = OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)

    k = settings.top_k
    report = evaluate_golden(golden, embedder, store, llm, k)

    print("\n=== Evaluation Report ===")
    for key, value in report.items():
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    report_path = Path(__file__).parent / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", report_path)

    # Gates are unconditional: the golden-set validation above guarantees both
    # subsets are non-empty, so neither metric can be skipped.
    failures = gate_failures(report, k)

    if failures:
        print(f"\nGATE FAILED: {'; '.join(failures)}")
        sys.exit(1)
    print("\nGATE PASSED")


if __name__ == "__main__":
    main()
