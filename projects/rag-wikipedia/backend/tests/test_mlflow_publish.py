"""Publishing a report to MLflow, with mlflow faked.

mlflow is an optional extra and is NOT installed in CI, so these patch
`sys.modules` the way tests/test_vertex.py does for the Google SDK. What is
under test is the mapping - which fields of a report become params, which
become metrics, and which numbers must never be averaged by accident.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from mlflow_publish import (  # noqa: E402
    main,
    metrics_of,
    params_of,
    publish,
    run_name,
    tags_of,
)

REPORT = {
    "suite": "serving_detail",
    "engine": "direct",
    "nli_model": "cross-encoder/nli-deberta-v3-base",
    "scored_git_sha": "9d14a3b",
    "provenance": {
        "git_sha": "2bcc858", "profile": "real", "collection": "wikipedia",
        "vector_count": 87173, "embed_model": "BAAI/bge-small-en-v1.5",
        "llm_model": "llama3.2:3b", "top_k": 5,
        "gates": {"recall": 0.8, "false_accept_rate_max": 0.1},
    },
    "conditions": {"gpu": "NVIDIA GeForce RTX 3060 Laptop GPU", "pause_at": 90},
    "metrics": {"false_accept_rate": 0.15, "refusal_accuracy": 0.85, "complete": True},
    "answers": {"supported_sentence_rate": 0.7925, "n_answered": 55},
    "latency": {"generated": {"p50_ms": 7328.0, "p95_ms": None}},
}


# --- the mapping --------------------------------------------------------------

def test_what_describes_the_run_becomes_a_parameter():
    params = params_of(REPORT)
    assert params["suite"] == "serving_detail"
    assert params["collection"] == "wikipedia"
    assert params["llm_model"] == "llama3.2:3b"


def test_both_commits_are_recorded_when_scoring_came_later():
    # A --score-only run judges old answers with current code; a report naming
    # one commit cannot be reproduced.
    params = params_of(REPORT)
    assert params["git_sha"] == "2bcc858"
    assert params["scored_git_sha"] == "9d14a3b"


def test_every_number_in_the_report_becomes_a_metric():
    metrics = metrics_of(REPORT)
    assert metrics["false_accept_rate"] == 0.15
    assert metrics["answers.supported_sentence_rate"] == 0.7925
    assert metrics["latency.generated.p50_ms"] == 7328.0


def test_a_flag_is_not_a_measurement():
    # `complete: true` logged as 1.0 would invite someone to average it.
    assert "complete" not in metrics_of(REPORT)


def test_a_missing_number_is_absent_rather_than_zero():
    # p95 is None when there are too few samples; zero would be a lie.
    assert "latency.generated.p95_ms" not in metrics_of(REPORT)


def test_the_gates_travel_with_the_run():
    tags = tags_of(REPORT, Path("serving_detail.direct.json"))
    assert json.loads(tags["gates"])["false_accept_rate_max"] == 0.1
    assert "RTX 3060" in tags["gpu"]


def test_the_run_is_named_for_what_it_measured():
    assert run_name(REPORT, Path("x.json")) == "serving_detail.direct"
    assert run_name({"status": "pass"}, Path("audit_report.json")) == "audit_report"


# --- the call -----------------------------------------------------------------

@pytest.fixture
def fake_mlflow(monkeypatch):
    module = MagicMock()
    module.start_run.return_value.__enter__.return_value.info.run_id = "abc123"
    monkeypatch.setitem(sys.modules, "mlflow", module)
    return module


def _write(tmp_path: Path, markdown: bool = False) -> Path:
    path = tmp_path / "serving_detail.direct.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")
    if markdown:
        path.with_suffix(".md").write_text("# report", encoding="utf-8")
    return path


def test_publishing_logs_params_metrics_and_tags(tmp_path, fake_mlflow):
    assert publish(_write(tmp_path)) == "abc123"
    fake_mlflow.log_params.assert_called_once()
    fake_mlflow.log_metrics.assert_called_once()
    fake_mlflow.set_tags.assert_called_once()


def test_the_markdown_report_is_attached_when_it_exists(tmp_path, fake_mlflow):
    publish(_write(tmp_path, markdown=True))
    assert fake_mlflow.log_artifact.call_count == 1


def test_no_markdown_is_not_an_error(tmp_path, fake_mlflow):
    publish(_write(tmp_path))
    fake_mlflow.log_artifact.assert_not_called()


def test_a_missing_report_is_an_error_not_an_empty_run(tmp_path, fake_mlflow):
    assert main([str(tmp_path / "nope.json")]) == 2
    fake_mlflow.start_run.assert_not_called()


def test_several_reports_publish_in_one_call(tmp_path, fake_mlflow):
    first = _write(tmp_path)
    second = tmp_path / "other.json"
    second.write_text(json.dumps(REPORT), encoding="utf-8")
    assert main([str(first), str(second)]) == 0
    assert fake_mlflow.start_run.call_count == 2


def test_importing_the_publisher_does_not_require_mlflow():
    # It is imported at the top of this file, on a machine where CI never
    # installs the extra. That it got this far is the assertion.
    assert "mlflow" not in sys.modules or isinstance(sys.modules.get("mlflow"), MagicMock)
