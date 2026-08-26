"""The regression gate must fire on real degradation and ignore noise.

A gate that never fires is a rubber stamp; one that fires on float wobble gets
ignored. Both failure modes are tested here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from compare_audit import TOLERANCE, compare  # noqa: E402


def _report(**overrides) -> dict:
    metrics = {
        "recall@5": 1.0,
        "precision@5": 0.9,
        "mrr": 0.95,
        "refusal_accuracy": 0.5,
        "false_accept_rate": 0.5,
        "answerable_refusal_rate": 0.0,
    }
    metrics.update(overrides)
    return {"datasets": {"golden": metrics}, "status": "suspect_overfit", "failures": []}


def test_identical_reports_show_no_regression():
    _, regressions = compare(_report(), _report())
    assert regressions == []


def test_a_drop_in_a_higher_is_better_metric_regresses():
    _, regressions = compare(_report(), _report(**{"recall@5": 0.82}))
    assert len(regressions) == 1
    assert "recall@5" in regressions[0]


def test_a_rise_in_an_inverted_metric_regresses():
    # false_accept_rate is lower-is-better: going UP is the regression. Treating
    # every metric as higher-is-better would miss exactly the failure this
    # project cares most about.
    _, regressions = compare(_report(), _report(false_accept_rate=0.62))
    assert len(regressions) == 1
    assert "false_accept_rate" in regressions[0]


def test_an_improvement_in_an_inverted_metric_does_not_regress():
    _, regressions = compare(_report(), _report(false_accept_rate=0.20))
    assert regressions == []


@pytest.mark.parametrize("delta", [TOLERANCE / 2, -TOLERANCE / 2])
def test_movement_within_tolerance_is_noise(delta):
    _, regressions = compare(_report(), _report(mrr=0.95 + delta))
    assert regressions == []


def test_a_missing_suite_is_a_regression():
    # Silently dropping a suite would otherwise read as "nothing got worse".
    candidate = _report()
    candidate["datasets"] = {}
    _, regressions = compare(_report(), candidate)
    assert any("missing" in r for r in regressions)


def test_a_new_suite_is_not_a_regression():
    candidate = _report()
    candidate["datasets"]["brand_new"] = dict(candidate["datasets"]["golden"])
    _, regressions = compare(_report(), candidate)
    assert regressions == []
