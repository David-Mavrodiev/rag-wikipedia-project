"""Measure refusal_min_evidence_coverage against a corpus, and pick a threshold.

`refusal_min_evidence_coverage` ships DISABLED (0.0). This is the script that is
supposed to un-disable it, and the reason the setting does not simply have a
default is in `app.core.idf.IdfTable.weight`: the weighting tightens as a corpus
grows, so a threshold fitted on the 2,422-chunk fixture is stricter on the
87,173-chunk serving collection. A default would be a number fitted to whichever
corpus the author happened to have.

    python scripts/sweep_coverage.py --collection wikipedia --suite serving_golden.jsonl

THE NUMBER TO BEAT is false_accept_rate 0.600, measured on the serving corpus
under the count gate. A candidate threshold has to beat that WITHOUT pushing
answerable_refusal_rate up to meet it — refusing everything scores 0.000 false
accepts and is worthless, which is why both columns are always printed together
and why --check enforces both.

Retrieval runs ONCE per question and the candidate list is cached; only the gate
is replayed per threshold. Re-embedding per threshold would make the sweep
minutes long and invite the shortcut of sweeping on a subset.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

DEFAULT_THRESHOLDS = [0.0, 0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8]

# The count gate's measured false_accept_rate per corpus, from `2c3725c`. Printed
# beside the sweep so a candidate threshold is read against what it replaces
# rather than against zero.
BASELINE_FALSE_ACCEPT = {"wikipedia": 0.600, "wikipedia_eval": 0.500}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="wikipedia")
    parser.add_argument("--suite", default="serving_golden.jsonl")
    parser.add_argument(
        "--check",
        type=float,
        default=None,
        metavar="THRESHOLD",
        help="exit non-zero unless THRESHOLD beats the corpus baseline without "
        "raising answerable_refusal_rate above --max-answerable-refusal",
    )
    parser.add_argument("--max-answerable-refusal", type=float, default=0.10)
    args = parser.parse_args(argv)

    from app.core import idf, runtime_config
    from app.core.config import settings

    settings.collection = args.collection

    from app.core.embeddings import BGEEmbedder
    from app.core.refusal import decide_evidence, is_private_or_time_dependent
    from app.core.retrieval import _rerank
    from app.core.vectorstore import QdrantStore

    suite_path = BACKEND / "eval" / args.suite
    cases = [
        json.loads(line)
        for line in suite_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    table = idf.load_table(args.collection)
    if table is None:
        print(
            f"No IDF table for {args.collection!r}. Run `make idf COLLECTION={args.collection}` "
            "first — without one the gate falls back to term counts and this sweep would "
            "report the same number at every threshold.",
            file=sys.stderr,
        )
        return 2

    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=args.collection)
    base = runtime_config.get_runtime_config()
    candidate_k = max(settings.top_k, base.retrieval_candidate_k)

    cached = []
    for case in cases:
        question = case["question"]
        # Mirrors retrieve() up to the gate, and no further: retrieve() applies a
        # token budget AFTER deciding, so calling it here would feed the replay a
        # different chunk list than the one the decision was made on.
        chunks = (
            []
            if is_private_or_time_dependent(question)
            else _rerank(question, store.search(embedder.embed(question), top_k=candidate_k))[
                : settings.top_k
            ]
        )
        cached.append((case, chunks))

    baseline = BASELINE_FALSE_ACCEPT.get(args.collection)
    print(
        f"collection={args.collection} suite={args.suite} cases={len(cached)} "
        f"idf_chunks={table.n_documents}"
    )
    if baseline is not None:
        print(f"count-gate baseline false_accept_rate = {baseline:.3f}  <- the number to beat")
    print(f"\n{'threshold':>10} {'false_accept':>13} {'answerable_refused':>19}")

    results: dict[float, tuple[float, float]] = {}
    thresholds = sorted(set(DEFAULT_THRESHOLDS + ([args.check] if args.check is not None else [])))
    for threshold in thresholds:
        runtime_config.apply_runtime_config(
            replace(base, refusal_min_evidence_coverage=threshold)
        )
        false_accepts = answerable_refused = n_unanswerable = n_answerable = 0
        for case, chunks in cached:
            refused = decide_evidence(case["question"], chunks).refused
            if case.get("expected_refusal"):
                n_unanswerable += 1
                false_accepts += not refused
            else:
                n_answerable += 1
                answerable_refused += refused

        far = false_accepts / n_unanswerable if n_unanswerable else 0.0
        arr = answerable_refused / n_answerable if n_answerable else 0.0
        results[threshold] = (far, arr)
        marker = ""
        if threshold == 0.0:
            marker = "  (gate disabled)"
        elif baseline is not None and far > baseline:
            marker = "  WORSE than the count gate"
        print(f"{threshold:>10.2f} {far:>13.4f} {arr:>19.4f}{marker}")

    if args.check is None:
        print(
            "\nNo threshold selected. Re-run with --check <value> to assert one, then set "
            "REFUSAL_MIN_EVIDENCE_COVERAGE and rerun the eval suites."
        )
        return 0

    far, arr = results[args.check]
    print(f"\n--check {args.check:.2f}: false_accept={far:.4f} answerable_refused={arr:.4f}")
    problems = []
    if baseline is not None and far >= baseline:
        problems.append(f"false_accept {far:.4f} does not beat the baseline {baseline:.3f}")
    if arr > args.max_answerable_refusal:
        problems.append(
            f"answerable_refusal {arr:.4f} exceeds {args.max_answerable_refusal:.2f} — "
            "this threshold buys its false-accept number by refusing real questions"
        )
    if problems:
        for problem in problems:
            print(f"  REJECTED: {problem}")
        return 1
    print("  ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
