from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.quality import update_quality_state
from app.core.runtime_config import (
    RetrievalRuntimeConfig,
    apply_runtime_config,
    get_runtime_config,
    rollback_runtime_config,
    save_runtime_config,
)
from eval.run_eval import evaluate_golden, gate_failures, write_markdown_report

MAX_GOLDEN_HOLDOUT_GAP = 0.10
DATASET_NAMES = ("golden", "holdout", "adversarial")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evaluate_datasets(base_dir: Path, embedder, store, *, k: int) -> dict[str, dict]:
    return {
        name: evaluate_golden(load_jsonl(base_dir / f"{name}.jsonl"), embedder, store, k=k)
        for name in DATASET_NAMES
    }


def audit_failures(reports: dict[str, dict], *, k: int) -> list[str]:
    failures: list[str] = []
    for name, report in reports.items():
        failures.extend(f"{name}: {failure}" for failure in gate_failures(report, k))

    gap = abs(reports["golden"][f"recall@{k}"] - reports["holdout"][f"recall@{k}"])
    if gap > MAX_GOLDEN_HOLDOUT_GAP:
        failures.append(f"golden_holdout_recall_gap={gap:.2f} > {MAX_GOLDEN_HOLDOUT_GAP}")

    return failures


def candidate_configs(base: RetrievalRuntimeConfig) -> list[RetrievalRuntimeConfig]:
    return [
        replace(base, refusal_min_score=max(0.35, base.refusal_min_score - 0.05)),
        replace(base, retrieval_candidate_k=max(base.retrieval_candidate_k, 30)),
        replace(
            base,
            refusal_min_score=max(0.35, base.refusal_min_score - 0.05),
            retrieval_candidate_k=max(base.retrieval_candidate_k, 30),
        ),
    ]


def try_auto_correct(base_dir: Path, embedder, store, *, k: int) -> tuple[bool, dict[str, dict]]:
    original = get_runtime_config()
    best_reports: dict[str, dict] = {}

    for candidate in candidate_configs(original):
        apply_runtime_config(candidate)
        reports = evaluate_datasets(base_dir, embedder, store, k=k)
        failures = audit_failures(reports, k=k)
        if not failures:
            save_runtime_config()
            return True, reports
        best_reports = reports
        rollback_runtime_config()

    apply_runtime_config(original)
    return False, best_reports


def summarize_reports(reports: dict[str, dict], *, k: int) -> dict:
    return {
        name: {
            f"recall@{k}": report[f"recall@{k}"],
            f"precision@{k}": report[f"precision@{k}"],
            "mrr": report["mrr"],
            "refusal_accuracy": report["refusal_accuracy"],
            "false_accept_rate": report["false_accept_rate"],
            "answerable_refusal_rate": report["answerable_refusal_rate"],
        }
        for name, report in reports.items()
    }


def write_audit_reports(
    base_dir: Path,
    reports: dict[str, dict],
    failures: list[str],
    *,
    k: int,
) -> None:
    audit_report = {
        "status": "healthy" if not failures else "suspect_overfit",
        "failures": failures,
        "datasets": summarize_reports(reports, k=k),
        "active_config": asdict(get_runtime_config()),
    }
    (base_dir / "audit_report.json").write_text(
        json.dumps(audit_report, indent=2),
        encoding="utf-8",
    )

    lines = ["# Evaluation Audit Report", "", f"- status: {audit_report['status']}"]
    lines.extend(f"- {failure}" for failure in failures)
    lines.append("")
    lines.append("## Dataset Summaries")
    for name, metrics in audit_report["datasets"].items():
        lines.append("")
        lines.append(f"### {name}")
        lines.extend(f"- {metric}: {value:.4f}" for metric, value in metrics.items())
    (base_dir / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")

    for name, report in reports.items():
        write_markdown_report(report, base_dir / f"{name}_report.md", k=k)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit golden-set overfit and runtime quality.")
    parser.add_argument("--auto-correct", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.vectorstore import QdrantStore

    args = parse_args(argv)
    base_dir = Path(__file__).parent
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    k = settings.top_k

    reports = evaluate_datasets(base_dir, embedder, store, k=k)
    failures = audit_failures(reports, k=k)

    if failures and args.auto_correct:
        corrected, corrected_reports = try_auto_correct(base_dir, embedder, store, k=k)
        if corrected:
            reports = corrected_reports
            failures = []

    status = "healthy" if not failures else "suspect_overfit"
    write_audit_reports(base_dir, reports, failures, k=k)
    update_quality_state(
        status=status,
        metrics=summarize_reports(reports, k=k),
        active_config=asdict(get_runtime_config()),
        reason="Audit passed." if not failures else "; ".join(failures),
    )

    print(json.dumps({"status": status, "failures": failures}, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
