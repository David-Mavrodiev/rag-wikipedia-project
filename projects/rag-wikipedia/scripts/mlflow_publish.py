"""Publish a finished eval report to MLflow. The harness is not touched.

    python scripts/mlflow_publish.py backend/eval/e2e/serving_detail.direct.json
    python scripts/mlflow_publish.py backend/eval/audit_report.json --experiment rag-audit
    MLFLOW_TRACKING_URI=file:./mlruns python scripts/mlflow_publish.py <report>…

WHY A PUBLISHER RATHER THAN INSTRUMENTATION. `run_eval.py`, `audit.py` and
`metrics.py` are watched by `audit_freshness`: an import added there forces a
re-measured audit of a corpus MLflow cannot affect. They already write reports
carrying provenance, conditions and metrics, so everything a tracking run needs
is on disk by the time they exit. Reading that is additive, it cannot change a
number, and it works **retroactively** on every report already committed -
which is the quickest way to have a history worth looking at.

What a run records:

    params    suite, engine, profile, collection, vector_count, embed_model,
              llm_model, top_k, the git sha that produced the answers, and the
              one that scored them when they differ
    metrics   every number in the report that is a number
    tags      the gates, so a run can be read against what it had to beat
    artifact  the markdown report beside the JSON, when there is one

mlflow is an OPTIONAL extra and is imported inside `publish`, so this file
imports - and its tests run - on a machine that has never installed it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_EXPERIMENT = "rag-wikipedia"
# Keys that describe the run rather than measure it.
PARAM_KEYS = ("suite", "engine", "nli_model", "profile", "collection", "vector_count",
              "embed_model", "llm_model", "top_k", "git_sha", "scored_git_sha",
              "generated_at", "scored_at", "status")
# Report sections whose numeric leaves are metrics.
METRIC_SECTIONS = ("metrics", "answers", "datasets", "summary")


def _flatten(value: Any, prefix: str = "") -> dict[str, float]:
    """Every number reachable in a report section, as `a.b.c -> value`.

    Booleans are skipped: `complete: true` is a fact about the run, not a
    measurement, and logging it as 1.0 invites averaging it later.
    """
    out: dict[str, float] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out |= _flatten(item, f"{prefix}{key}.")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out[prefix.rstrip(".")] = float(value)
    return out


def read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def params_of(report: dict) -> dict[str, str]:
    flat = {**report, **report.get("provenance", {})}
    return {key: str(flat[key]) for key in PARAM_KEYS if flat.get(key) is not None}


def metrics_of(report: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for section in METRIC_SECTIONS:
        if isinstance(report.get(section), dict):
            prefix = "" if section == "metrics" else f"{section}."
            out |= _flatten(report[section], prefix)
    # Latency lives one level deeper and is worth having beside the rest.
    if isinstance(report.get("latency"), dict):
        out |= _flatten(report["latency"], "latency.")
    return out


def tags_of(report: dict, path: Path) -> dict[str, str]:
    gates = report.get("provenance", {}).get("gates") or {}
    tags = {"report": path.name, "source": "eval-harness"}
    if gates:
        tags["gates"] = json.dumps(gates, sort_keys=True)
    if report.get("conditions", {}).get("gpu"):
        tags["gpu"] = str(report["conditions"]["gpu"])
    return tags


def run_name(report: dict, path: Path) -> str:
    suite = report.get("suite") or path.stem
    engine = report.get("engine")
    return f"{suite}.{engine}" if engine else str(suite)


def publish(path: Path, experiment: str = DEFAULT_EXPERIMENT,
            tracking_uri: str | None = None) -> str:
    """Log one report as one MLflow run; return its run id."""
    import mlflow  # noqa: PLC0415 - optional extra, imported where it is used

    report = read_report(path)
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name(report, path)) as run:
        mlflow.log_params(params_of(report))
        mlflow.log_metrics(metrics_of(report))
        mlflow.set_tags(tags_of(report, path))
        markdown = path.with_suffix(".md")
        if markdown.exists():
            mlflow.log_artifact(str(markdown))
        return run.info.run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--tracking-uri", default=None,
                        help="default: MLFLOW_TRACKING_URI, else ./mlruns")
    args = parser.parse_args(argv)

    missing = [path for path in args.reports if not path.exists()]
    if missing:
        print(f"no such report: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2
    for path in args.reports:
        run_id = publish(path, args.experiment, args.tracking_uri)
        print(f"{path.name} -> run {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
