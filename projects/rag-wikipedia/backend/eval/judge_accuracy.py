"""Measure the JUDGE, not the RAG.

`eval/grounding.py` decides the published groundedness number, and until now its
errors were only ever found by reading the answers it flagged. That is circular:
a judge tuned on the claims used to find its mistakes would fit 53 claims.

This runs a judge over claims whose labels are fixed independently and reports
what it gets right:

  real       `eval/judge/real_claims.jsonl.gz` - claims from end-to-end runs,
             each read against its full retrieved context by hand, labelled by
             the rules in `eval/judge/LABELS.md`
  synthetic  `eval/judge/synthetic.jsonl.gz` - claims built from passages no
             suite uses, labelled by construction (`scripts/build_judge_set.py`)

Both carry a dev/test split, and both are reported per `kind` so one noisy kind
cannot hide inside an average. Three numbers matter:

  agreement   how often the verdict matches the label
  precision   of the claims it flags, how many are really unsupported - the
              number that says whether a flag is worth reading
  recall      of the unsupported claims, how many it flags - the number that
              says whether the groundedness rate is a floor or a fiction

Name-only claims ("Party on the Enterprise") are counted apart: an NLI model is
not being asked a proposition, and the measured scores for them range from 0.03
to 0.99.

  uv run python eval/judge_accuracy.py --set real --split dev
  uv run python eval/judge_accuracy.py --set synthetic --split test --assemble 3
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.grounding import SUPPORT_THRESHOLD, sentence_support  # noqa: E402

logger = logging.getLogger("judge_accuracy")

JUDGE_DIR = Path(__file__).parent / "judge"
SETS = {"real": "real_claims.jsonl.gz", "synthetic": "synthetic.jsonl.gz"}
PROPOSITIONAL = ("supported", "unsupported", "contradicted")
CALIBRATION_BINS = 5


def load_claims(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def score_claims(claims: list[dict], judge, *, assemble: int = 0) -> list[float]:
    """One claim at a time: each has its own context, so they cannot be batched."""
    scores: list[float] = []
    for index, claim in enumerate(claims, 1):
        contexts = [chunk["text"] for chunk in claim["context"]]
        support = sentence_support([claim["claim"]], contexts, judge, assemble=assemble)
        scores.append(round(support[0], 4))
        if index % 20 == 0:
            logger.info("  scored %s/%s claims", index, len(claims))
    return scores


def _rates(flagged_unsupported: int, flagged_supported: int, missed: int) -> dict:
    precision = (flagged_unsupported / (flagged_unsupported + flagged_supported)
                 if flagged_unsupported + flagged_supported else None)
    recall = (flagged_unsupported / (flagged_unsupported + missed)
              if flagged_unsupported + missed else None)
    return {"precision": precision, "recall": recall}


def confusion(claims: list[dict], scores: list[float], *, threshold: float) -> dict:
    """Agreement, precision and recall over the propositional claims."""
    judged = [(c, s) for c, s in zip(claims, scores) if c["label"] in PROPOSITIONAL]
    tp = [c["id"] for c, s in judged if c["label"] != "supported" and s < threshold]
    fp = [c["id"] for c, s in judged if c["label"] == "supported" and s < threshold]
    fn = [c["id"] for c, s in judged if c["label"] != "supported" and s >= threshold]
    tn = [c["id"] for c, s in judged if c["label"] == "supported" and s >= threshold]
    other = [(c, s) for c, s in zip(claims, scores) if c["label"] not in PROPOSITIONAL]
    return {
        "n": len(judged),
        "threshold": threshold,
        "agreement": (len(tp) + len(tn)) / len(judged) if judged else None,
        **_rates(len(tp), len(fp), len(fn)),
        "flagged_and_unsupported": tp,
        "flagged_but_supported": fp,
        "passed_but_unsupported": fn,
        "name_only": {c["id"]: s for c, s in other},
    }


def per_kind(claims: list[dict], scores: list[float], *, threshold: float) -> dict:
    """Agreement within each kind of claim, so an average cannot hide a mode."""
    kinds: dict[str, list[tuple[dict, float]]] = {}
    for claim, score in zip(claims, scores):
        kinds.setdefault(claim.get("kind") or claim["label"], []).append((claim, score))
    out = {}
    for kind, rows in sorted(kinds.items()):
        correct = sum((score < threshold) == (claim["label"] != "supported")
                      for claim, score in rows if claim["label"] in PROPOSITIONAL)
        counted = sum(claim["label"] in PROPOSITIONAL for claim, _ in rows)
        out[kind] = {"n": len(rows), "agreement": correct / counted if counted else None}
    return out


def calibration(claims: list[dict], scores: list[float], *, bins: int = CALIBRATION_BINS) -> dict:
    """Does a score of 0.8 mean 80% supported? Expected calibration error.

    Reported because a calibrated score can carry a review band - "act, check,
    refuse" - where a raw verdict cannot.
    """
    rows = [(s, c["label"] == "supported") for c, s in zip(claims, scores)
            if c["label"] in PROPOSITIONAL]
    if not rows:
        return {"bins": [], "ece": None}
    table = []
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        last = index == bins - 1
        bucket = [(s, ok) for s, ok in rows if low <= s < high or (last and s == 1.0)]
        if not bucket:
            continue
        mean_score = sum(s for s, _ in bucket) / len(bucket)
        share_supported = sum(ok for _, ok in bucket) / len(bucket)
        table.append({"from": low, "to": high, "n": len(bucket),
                      "mean_score": round(mean_score, 4),
                      "share_supported": round(share_supported, 4)})
        error += len(bucket) / len(rows) * abs(mean_score - share_supported)
    return {"bins": table, "ece": round(error, 4)}


def _fmt(value, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def build_markdown(report: dict) -> str:
    c, kinds, cal = report["confusion"], report["per_kind"], report["calibration"]
    lines = [
        f"## Judge accuracy: `{report['set']}` / {report['split']}",
        "",
        f"_judge `{report['judge']}` | assemble {report['assemble']} | threshold "
        f"{c['threshold']} | {c['n']} propositional claims, "
        f"{len(c['name_only'])} name-only_",
        "",
        "| | value |",
        "|---|---:|",
        f"| agreement | {_fmt(c['agreement'])} |",
        f"| precision of an unsupported flag | {_fmt(c['precision'])} |",
        f"| recall of unsupported claims | {_fmt(c['recall'])} |",
        f"| expected calibration error | {_fmt(cal['ece'])} |",
        "",
        "| kind | n | agreement |",
        "|---|---:|---:|",
    ]
    lines += [f"| `{kind}` | {row['n']} | {_fmt(row['agreement'])} |"
              for kind, row in kinds.items()]
    lines += ["", "| score | n | share actually supported |", "|---|---:|---:|"]
    lines += [f"| {b['from']:.1f}-{b['to']:.1f} | {b['n']} | {b['share_supported']:.3f} |"
              for b in cal["bins"]]
    if c["passed_but_unsupported"]:
        lines += ["", "**Passed but unsupported** (what the rate does not see): "
                  + ", ".join(f"`{cid}`" for cid in c["passed_but_unsupported"])]
    if c["flagged_but_supported"]:
        lines += ["", "**Flagged but supported** (what a reader would waste time on): "
                  + ", ".join(f"`{cid}`" for cid in c["flagged_but_supported"])]
    if c["name_only"]:
        scored = ", ".join(f"`{cid}` {score:.2f}" for cid, score in c["name_only"].items())
        lines += ["", f"**Name-only claims**, counted apart: {scored}"]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", dest="which", default="real", choices=[*SETS, "path"])
    parser.add_argument("--path", type=Path, help="a labelled set outside eval/judge/")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--assemble", type=int, default=0,
                        help="judge variant: 0 is the published judge; N assembles the "
                             "N sentences of a chunk that best overlap the claim")
    parser.add_argument("--threshold", type=float, default=SUPPORT_THRESHOLD)
    parser.add_argument("--nli-model", default=None)
    parser.add_argument("--nli-device", default="cpu")
    parser.add_argument("--limit", type=int, default=None,
                        help="first N claims: a smoke test, NOT a measurement")
    parser.add_argument("--out-dir", type=Path, default=JUDGE_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)

    path = args.path if args.which == "path" else JUDGE_DIR / SETS[args.which]
    if not path.exists():
        logger.error("%s does not exist - build it first (scripts/build_judge_set.py)", path)
        return 2
    claims = [c for c in load_claims(path)
              if args.split == "all" or c.get("split") == args.split]
    if not claims:
        logger.error("no claims in split %s of %s", args.split, path.name)
        return 2
    if args.limit:
        claims = claims[: args.limit]

    from eval.grounding import DEFAULT_NLI_MODEL, NLIJudge

    judge = NLIJudge(args.nli_model or DEFAULT_NLI_MODEL, device=args.nli_device)
    scores = score_claims(claims, judge, assemble=args.assemble)
    report = {
        "set": path.stem.replace(".jsonl", ""),
        "split": args.split,
        "judge": judge.model_name,
        "assemble": args.assemble,
        "labels": dict(Counter(c["label"] for c in claims)),
        "confusion": confusion(claims, scores, threshold=args.threshold),
        "per_kind": per_kind(claims, scores, threshold=args.threshold),
        "calibration": calibration(claims, scores),
        "scores": {c["id"]: s for c, s in zip(claims, scores)},
        "limit": args.limit,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"accuracy.{report['set']}.{args.split}.assemble{args.assemble}"
    (args.out_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = build_markdown(report)
    (args.out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    if args.limit:
        print("NOTE: --limit was set, so this is a smoke test and not a measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
