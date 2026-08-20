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
