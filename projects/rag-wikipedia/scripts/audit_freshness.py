"""Fail if the committed audit report predates a change that could invalidate it.

`audit_report.json` is a generated measurement kept in version control, because
`GET /quality` serves it before any audit has run in a process. A committed
measurement rots silently: the code that produced the numbers moves on and the
file keeps asserting the old ones.

This does not re-run the audit - that needs a live Qdrant with an ingested
corpus, which CI does not have yet. It asks a cheaper question that needs
nothing but git:

    has anything that can change these numbers been committed since the
    report was generated?

If so, the report is stale and a human must run `make eval-audit` and commit the
result. Deliberately not automated into a bot commit: a change in measured
quality should be a reviewed line in a diff, not something automation slips onto
the default branch.

Usage:
    python scripts/audit_freshness.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
REPORT = PROJECT / "backend" / "eval" / "audit_report.json"

# Paths whose content can move the metrics. Retrieval and refusal decide what is
# returned; the suites decide what is asked; the eval harness decides how it is
# scored. Prompt and LLM code is absent on purpose - the audit is retrieval-only
# unless an llm is passed, so generation cannot change these numbers.
WATCHED = [
    "projects/rag-wikipedia/backend/app/core/refusal.py",
    "projects/rag-wikipedia/backend/app/core/retrieval.py",
    "projects/rag-wikipedia/backend/app/core/chunking.py",
    "projects/rag-wikipedia/backend/app/core/embeddings.py",
    "projects/rag-wikipedia/backend/app/core/vectorstore.py",
    "projects/rag-wikipedia/backend/app/core/config.py",
    "projects/rag-wikipedia/backend/app/core/runtime_config.py",
    "projects/rag-wikipedia/backend/eval/run_eval.py",
    "projects/rag-wikipedia/backend/eval/audit.py",
    "projects/rag-wikipedia/backend/eval/metrics.py",
    "projects/rag-wikipedia/backend/eval/golden.jsonl",
    "projects/rag-wikipedia/backend/eval/holdout.jsonl",
    "projects/rag-wikipedia/backend/eval/adversarial.jsonl",
]

REGENERATE = (
    "Run `make eval-audit` against an ingested corpus and commit the result.\n"
    "  The report records the corpus it measured, so regenerate it against the\n"
    "  one you intend to publish - not whatever happens to be in a local Qdrant."
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def _is_shallow() -> bool:
    return _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def main() -> int:
    if not REPORT.exists():
        print(f"audit-freshness FAILED\n\n  {REPORT} is missing.\n  {REGENERATE}")
        return 1

    try:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"audit-freshness FAILED\n\n  {REPORT} is not valid JSON: {exc}")
        return 1

    sha = (report.get("provenance") or {}).get("git_sha")
    if not sha:
        print(
            "audit-freshness FAILED\n\n"
            "  The report records no provenance.git_sha, so there is no way to tell\n"
            "  which revision produced these numbers.\n"
            f"  {REGENERATE}"
        )
        return 1

    if _is_shallow():
        # A shallow clone cannot answer "is X an ancestor of Y". Say so loudly
        # rather than passing by accident - a check that silently degrades to
        # always-green is worse than no check.
        print(
            "audit-freshness SKIPPED\n\n"
            "  This is a shallow clone, so commit ancestry cannot be resolved.\n"
            "  Set `fetch-depth: 0` on actions/checkout to enable this gate."
        )
        return 0

    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        print(
            "audit-freshness FAILED\n\n"
            f"  The report was generated at {sha[:12]}, which is not in this\n"
            "  repository - it was amended, rebased away, or never pushed.\n"
            f"  {REGENERATE}"
        )
        return 1

    stale: list[tuple[str, str, str]] = []
    for path in WATCHED:
        last = _git("log", "-1", "--format=%H %h %s", "--", path).stdout.strip()
        if not last:
            continue
        full = last.split(" ", 1)[0]
        # Fresh iff the last change to this path is an ancestor of (or is) the
        # revision the report was generated at.
        if _git("merge-base", "--is-ancestor", full, sha).returncode != 0:
            short, subject = last.split(" ", 2)[1:]
            stale.append((path, short, subject))

    if stale:
        print("audit-freshness FAILED\n")
        print(f"  audit_report.json was generated at {sha[:12]}, but these have")
        print("  changed since, and each can move the measured numbers:\n")
        width = max(len(Path(p).name) for p, _, _ in stale)
        for path, short, subject in stale:
            print(f"    {Path(path).name:<{width}}  {short}  {subject[:52]}")
        print(f"\n  {REGENERATE}")
        return 1

    prov = report["provenance"]
    print(
        "audit-freshness OK - report at {sha} measured {n} vectors "
        "of profile '{profile}'; nothing affecting it has changed since".format(
            sha=sha[:12],
            n=prov.get("vector_count"),
            profile=prov.get("profile"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
