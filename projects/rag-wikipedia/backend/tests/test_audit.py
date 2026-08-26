from pathlib import Path

from eval import audit


def _report(recall: float) -> dict:
    return {
        "recall@5": recall,
        "precision@5": 1.0,
        "mrr": 1.0,
        "refusal_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "answerable_refusal_rate": 0.0,
    }


def test_audit_detects_golden_holdout_gap():
    reports = {
        "golden": _report(1.0),
        "holdout": _report(0.7),
        "adversarial": _report(1.0),
    }

    failures = audit.audit_failures(reports, k=5)

    assert any("golden_holdout_recall_gap" in failure for failure in failures)


def test_audit_passes_when_all_datasets_are_consistent():
    reports = {
        "golden": _report(0.9),
        "holdout": _report(0.85),
        "adversarial": _report(0.88),
    }

    assert audit.audit_failures(reports, k=5) == []


def test_failed_auto_correct_restores_the_original_config_and_returns_no_reports(monkeypatch):
    # The last candidate's numbers describe a config that is no longer active.
    # Returning them invites a caller to publish metrics that contradict the
    # active_config printed beside them.
    from app.core import runtime_config as rc

    original = rc.get_runtime_config()
    monkeypatch.setattr(audit, "evaluate_datasets", lambda base_dir, e, s, k: {"golden": {}})
    monkeypatch.setattr(audit, "audit_failures", lambda reports, k: ["still failing"])

    corrected, reports = audit.try_auto_correct(Path("."), object(), object(), k=5)

    assert corrected is False
    assert reports == {}
    assert rc.get_runtime_config() == original


def test_successful_auto_correct_returns_reports_for_the_active_config(monkeypatch):
    from app.core import runtime_config as rc

    original = rc.get_runtime_config()
    measured = {"golden": {"recall@5": 1.0}}
    monkeypatch.setattr(audit, "evaluate_datasets", lambda base_dir, e, s, k: measured)
    monkeypatch.setattr(audit, "audit_failures", lambda reports, k: [])
    monkeypatch.setattr(audit, "save_runtime_config", lambda: None)

    corrected, reports = audit.try_auto_correct(Path("."), object(), object(), k=5)

    assert corrected is True
    assert reports == measured
    assert rc.get_runtime_config() != original  # a candidate is now active

    rc.apply_runtime_config(original)


def test_load_jsonl_skips_blank_and_whitespace_only_lines(tmp_path):
    # `if line` is only falsy for a truly empty string, so a line of spaces
    # reached json.loads(" ") and raised.
    path = tmp_path / "suite.jsonl"
    path.write_text(
        '{"question": "a"}\n'
        "\n"
        "   \n"
        "\t\n"
        '  {"question": "b"}  \n',
        encoding="utf-8",
    )

    assert audit.load_jsonl(path) == [{"question": "a"}, {"question": "b"}]
