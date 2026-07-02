from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.retrieval import retrieve
    from app.core.vectorstore import QdrantStore
    from eval.metrics import mrr, recall_at_k, reciprocal_rank

    golden_path = Path(__file__).parent / "golden.jsonl"
    golden = [json.loads(line) for line in golden_path.read_text().splitlines() if line.strip()]

    if not golden:
        logger.warning("golden.jsonl is empty — nothing to evaluate.")
        return

    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)

    k = settings.top_k
    recall_scores: list[float] = []
    reciprocal_rank_scores: list[float] = []

    for item in golden:
        question = item["question"]
        expected = item["expected_sources"]
        chunks, _ = retrieve(question, embedder, store, top_k=k)
        texts = [chunk["text"] for chunk in chunks]

        recall_score = recall_at_k(texts, expected, k=k)
        rr_score = reciprocal_rank(texts, expected)
        recall_scores.append(recall_score)
        reciprocal_rank_scores.append(rr_score)

        logger.info("Q: %r | recall@%s=%.2f | RR=%.2f", question[:50], k, recall_score, rr_score)

    n = len(golden)
    report = {
        f"recall@{k}": sum(recall_scores) / n,
        "mrr": mrr(reciprocal_rank_scores),
        "n_questions": n,
    }

    print("\n=== Evaluation Report ===")
    for key, value in report.items():
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    report_path = Path(__file__).parent / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", report_path)


if __name__ == "__main__":
    main()
