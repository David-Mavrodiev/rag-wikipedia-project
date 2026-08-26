"""Verify the documentation's factual claims against the repository.

The test count alone was documented six different ways across seven files.
Hand-copied facts drift; this makes them checkable.

Two mechanisms, on purpose:

* GENERATED BLOCKS - a region fenced by
  ``<!-- docs-check:begin NAME -->`` / ``<!-- docs-check:end -->`` is rewritten
  from the repository. Good for inventories and tables.
* ASSERTED CLAIMS - a regex per fact, matched anywhere in the docs. Good for
  numbers that must stay inline and readable, like the lesson script's
  "**192 passing**", which nobody wants to replace with a pointer.

Usage:
    python scripts/docs_check.py            # verify; non-zero exit on drift
    python scripts/docs_check.py --write    # regenerate the generated blocks
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # rag-wikipedia-project/
PROJECT = Path(__file__).resolve().parents[1]       # projects/rag-wikipedia/
BACKEND = PROJECT / "backend"


# --------------------------------------------------------------------------
# facts, derived from the repository rather than from prose
# --------------------------------------------------------------------------
def collect_tests() -> tuple[int, dict[str, int]]:
    """Return (total, {test_file: count}) via pytest's own collector.

    Counting `def test_` with grep is wrong - parametrize expands one function
    into many cases, which is how a 133-vs-179 discrepancy appears.
    """
    out = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q"],
        cwd=BACKEND, capture_output=True, text=True, check=False,
    ).stdout
    per: dict[str, int] = {}
    for line in out.splitlines():
        if "::" not in line:
            continue
        f = line.split("::", 1)[0].strip().replace("\\", "/")
        per[f.rsplit("/", 1)[-1]] = per.get(f.rsplit("/", 1)[-1], 0) + 1
    return sum(per.values()), dict(sorted(per.items(), key=lambda kv: (-kv[1], kv[0])))


def frontend_tests() -> tuple[int, dict[str, int]]:
    per = {}
    for f in sorted((PROJECT / "frontend/src/components").glob("*.test.tsx")):
        per[f.name] = len(re.findall(r"^test\(", f.read_text(encoding="utf-8"), re.M))
    return sum(per.values()), per


def suite_sizes() -> dict[str, tuple[int, int, int]]:
    sizes = {}
    for name in ("golden", "holdout", "adversarial"):
        rows = [
            json.loads(line)
            for line in (BACKEND / "eval" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        un = sum(1 for r in rows if r.get("expected_refusal"))
        sizes[name] = (len(rows), len(rows) - un, un)
    return sizes


def gates() -> dict[str, float]:
    tree = ast.parse((BACKEND / "eval" / "run_eval.py").read_text(encoding="utf-8"))
    want = {"RECALL_GATE", "PRECISION_GATE", "REFUSAL_GATE", "FALSE_ACCEPT_MAX"}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in want:
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def settings_fields() -> list[str]:
    tree = ast.parse((BACKEND / "app" / "core" / "config.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return [
                n.target.id
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                and n.target.id != "model_config"
            ]
    return []


def compose_services() -> list[str]:
    text = (PROJECT / "docker-compose.yml").read_text(encoding="utf-8")
    body = text.split("\nvolumes:", 1)[0]
    return re.findall(r"^  ([a-z0-9_-]+):$", body, re.M)


# --------------------------------------------------------------------------
# generated blocks
# --------------------------------------------------------------------------
def block_test_inventory() -> str:
    total, per = collect_tests()
    ftotal, fper = frontend_tests()
    lines = [f"Backend: **{total} tests** across {len(per)} files.", "", "```text"]
    lines += [f"{name:24} {n}" for name, n in per.items()]
    lines += ["```", "", f"Frontend: **{ftotal} tests** across {len(fper)} files.", "", "```text"]
    lines += [f"{name:24} {n}" for name, n in fper.items()]
    lines += ["```"]
    return "\n".join(lines)


def block_eval_suites() -> str:
    lines = ["| suite | cases | answerable | unanswerable |", "|---|---:|---:|---:|"]
    for name, (t, a, u) in suite_sizes().items():
        lines.append(f"| `{name}.jsonl` | {t} | {a} | {u} |")
    g = gates()
    lines += ["", "Gates enforced (all unconditional):", ""]
    lines += [
        f"- `recall@k >= {g['RECALL_GATE']}`",
        f"- `precision@k >= {g['PRECISION_GATE']}`",
        f"- `refusal_accuracy >= {g['REFUSAL_GATE']}`",
        f"- `false_accept_rate <= {g['FALSE_ACCEPT_MAX']}`",
    ]
    return "\n".join(lines)


def block_settings() -> str:
    fields = settings_fields()
    lines = [f"`Settings` exposes **{len(fields)} settings** "
             "(env var = the upper-case name); see `backend/.env.example`.", "", "```text"]
    lines += [f"{f.upper()}" for f in fields]
    lines += ["```"]
    return "\n".join(lines)


def block_services() -> str:
    svc = compose_services()
    return (f"Compose runs **{len(svc)} services**: "
            + ", ".join(f"`{s}`" for s in svc) + ".")


BLOCKS = {
    "test-inventory": block_test_inventory,
    "eval-suites": block_eval_suites,
    "settings": block_settings,
    "services": block_services,
}

BEGIN = "<!-- docs-check:begin {name} -->"
END = "<!-- docs-check:end -->"


def docs() -> list[Path]:
    paths = sorted(ROOT.glob("*.md")) + sorted((PROJECT / "docs").glob("*.md"))
    paths.append(PROJECT / "README.md")
    return [p for p in paths if p.is_file()]


def sync_blocks(write: bool) -> list[str]:
    problems = []
    for path in docs():
        text = path.read_text(encoding="utf-8")
        updated = text
        for name, fn in BLOCKS.items():
            pattern = re.compile(
                re.escape(BEGIN.format(name=name)) + r"\n.*?\n" + re.escape(END), re.S
            )
            if not pattern.search(updated):
                continue
            fresh = BEGIN.format(name=name) + "\n" + fn() + "\n" + END
            updated = pattern.sub(lambda _m: fresh, updated)
        if updated != text:
            if write:
                path.write_text(updated, encoding="utf-8")
            else:
                problems.append(f"{path.relative_to(ROOT)}: generated block is stale")
    return problems


# --------------------------------------------------------------------------
# asserted claims
# --------------------------------------------------------------------------
def assertions() -> list[tuple[str, re.Pattern[str], str]]:
    total, per = collect_tests()
    ftotal, _ = frontend_tests()
    g = gates()
    checks = [
        ("backend test count",
         re.compile(r"\*\*(\d+)\s+(?:backend\s+)?(?:pytest\s+)?(?:tests|passing)\*\*"), str(total)),
        ("frontend test count",
         re.compile(r"\*\*(\d+)\s+frontend\s+\w*\s*(?:tests|component tests)\*\*"), str(ftotal)),
    ]
    return [(label, pat, expected) for label, pat, expected in checks]


GENERATED = re.compile(
    re.escape("<!-- docs-check:begin ") + r".*?" + re.escape(END), re.S
)


def _without_generated(text: str) -> str:
    """Blank out generated regions, preserving offsets so line numbers stay right.

    Generated blocks are rewritten from the repository, so they are true by
    construction. Asserting over them also causes false hits - the backend
    count pattern matched the frontend line of the inventory block.
    """
    return GENERATED.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def check_claims() -> list[str]:
    problems = []
    for label, pattern, expected in assertions():
        for path in docs():
            text = _without_generated(path.read_text(encoding="utf-8"))
            for m in pattern.finditer(text):
                if m.group(1) != expected:
                    line = text[: m.start()].count("\n") + 1
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line}: {label} says {m.group(1)}, "
                        f"repository says {expected}  ({m.group(0)!r})"
                    )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate generated blocks in place")
    args = ap.parse_args()

    problems = sync_blocks(write=args.write) + check_claims()
    if problems:
        print("docs-check FAILED\n")
        for p in problems:
            print("  " + p)
        print("\nRun `python scripts/docs_check.py --write` to refresh generated blocks;")
        print("asserted claims must be corrected by hand.")
        return 1

    total, _ = collect_tests()
    print(f"docs-check OK - {total} backend tests, claims and generated blocks agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
