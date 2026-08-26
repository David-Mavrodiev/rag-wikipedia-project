"""Compare a fresh audit against the committed baseline.

Two jobs, deliberately separated:

* Render the delta as a markdown table, so a reviewer sees
  `refusal_accuracy 0.55 -> 0.50` in the pull request rather than discovering it
  after merge.
* Fail on **regression**, not on the absolute gates.

Why regression rather than the absolute gates: the absolute gates are currently
red on purpose. The eval suites were rewritten to ask questions this corpus can
actually answer, which exposed a real weakness in the evidence gate - false
accepts around 0.45-0.50 - that no threshold configuration fixes without also
refusing "Who was Abraham Lincoln?". Gating on the absolute thresholds would
therefore fail every build and teach everyone to ignore the signal.

Gating on regression asks the question that is actually actionable on a pull
request: *did this change make it worse than the recorded baseline?* The
absolute gates remain in the report as the target, and `audit.py` still records
them as failures - they are simply not what blocks a merge today.

Usage:
    python scripts/compare_audit.py --baseline <committed.json> --candidate <fresh.json>
    python scripts/compare_audit.py ... --markdown out.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Metrics where a HIGHER value is better. false_accept_rate and
# answerable_refusal_rate are inverted - lower is better - so they are listed
# separately rather than special-cased at the comparison site.
HIGHER_IS_BETTER = ("recall@5", "precision@5", "mrr", "refusal_accuracy")
LOWER_IS_BETTER = ("false_accept_rate", "answerable_refusal_rate")

# Metrics move a little between runs for reasons that are not code: ties in
# vector scores, float accumulation. This is the amount of movement treated as
# noise rather than regression.
TOLERANCE = 0.01


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _arrow(delta: float, higher_better: bool) -> str:
    if abs(delta) < 1e-9:
        return "="
    improved = delta > 0 if higher_better else delta < 0
    return "improved" if improved else "WORSE"


def compare(baseline: dict, candidate: dict) -> tuple[list[str], list[str]]:
    """Return (markdown_lines, regressions)."""
    base_sets = baseline.get("datasets") or {}
    cand_sets = candidate.get("datasets") or {}

    lines = ["| suite | metric | baseline | candidate | delta | |", "|---|---|---:|---:|---:|---|"]
    regressions: list[str] = []

    for suite in sorted(set(base_sets) | set(cand_sets)):
        base = base_sets.get(suite) or {}
        cand = cand_sets.get(suite) or {}
        if not base:
            lines.append(f"| `{suite}` | — | — | new suite | — | |")
            continue
        if not cand:
            regressions.append(f"{suite}: present in the baseline, missing from this run")
            continue

        for metric in (*HIGHER_IS_BETTER, *LOWER_IS_BETTER):
            if metric not in base or metric not in cand:
                continue
            before, after = float(base[metric]), float(cand[metric])
            delta = after - before
            higher_better = metric in HIGHER_IS_BETTER
            verdict = _arrow(delta, higher_better)

            worse = (delta < -TOLERANCE) if higher_better else (delta > TOLERANCE)
            if worse:
                regressions.append(
                    f"{suite}.{metric}: {before:.3f} -> {after:.3f} "
                    f"({delta:+.3f}, tolerance {TOLERANCE})"
                )
                verdict = "**REGRESSED**"
            elif abs(delta) <= TOLERANCE:
                verdict = "="

            mark = "" if verdict == "=" else verdict
            lines.append(
                f"| `{suite}` | {metric} | {before:.3f} | {after:.3f} | {delta:+.3f} | {mark} |"
            )

    return lines, regressions


def provenance_note(report: dict, label: str) -> str:
    prov = report.get("provenance") or {}
    if not prov:
        return f"_{label}: no provenance recorded._"
    return (
        f"_{label}: {prov.get('vector_count')} vectors, profile "
        f"`{prov.get('profile')}`, collection `{prov.get('collection')}`, "
        f"`{prov.get('embed_model')}`, k={prov.get('top_k')}, "
        f"at `{str(prov.get('git_sha'))[:12]}`._"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--candidate", required=True, type=Path)
    ap.add_argument("--markdown", type=Path, help="also write the report here")
    args = ap.parse_args()

    baseline, candidate = _load(args.baseline), _load(args.candidate)
    lines, regressions = compare(baseline, candidate)

    out = ["## Evaluation delta", ""]
    out += lines
    out += [
        "",
        provenance_note(baseline, "baseline"),
        provenance_note(candidate, "this run"),
        "",
    ]

    status = candidate.get("status")
    failures = candidate.get("failures") or []
    out.append(f"Absolute gates: **{status}**" + (f" — {len(failures)} unmet" if failures else ""))
    if failures:
        out.append("")
        out += [f"- {f}" for f in failures]
        out += [
            "",
            "_The absolute gates are a known-red target, not the merge condition._",
            "_See `scripts/compare_audit.py` for why this gates on regression instead._",
        ]

    if regressions:
        out += ["", "### Regressions against the baseline", ""]
        out += [f"- {r}" for r in regressions]

    report = "\n".join(out)
    print(report)
    if args.markdown:
        args.markdown.write_text(report + "\n", encoding="utf-8")

    if regressions:
        print(f"\ncompare-audit FAILED - {len(regressions)} regression(s)", file=sys.stderr)
        return 1
    print("\ncompare-audit OK - no metric regressed beyond tolerance", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
