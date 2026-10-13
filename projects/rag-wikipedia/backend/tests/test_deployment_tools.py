"""The two deployment tools that can be wrong quietly.

The corpus check decides whether a host is allowed to serve: a demo answering
from a different corpus than the published numbers describe is worse than one
that does not start. The watchdog decides whether a day's spending stops the
GPU. Both are cheap to test and expensive to get wrong.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "modal"))

import corpus_snapshot  # noqa: E402
from watchdog import DEFAULT_CEILING_USD, judge  # noqa: E402

# --- the corpus check ---------------------------------------------------------

def _info(points=87173, status="green", size=384):
    return {"result": {"points_count": points, "status": status,
                       "config": {"params": {"vectors": {"size": size}}}}}


@pytest.fixture
def served(monkeypatch):
    def _serve(payload):
        monkeypatch.setattr(corpus_snapshot, "_api",
                            lambda *a, **k: payload if isinstance(payload, dict) else payload())
    return _serve


def test_the_expected_corpus_passes(served, capsys):
    served(_info())
    assert corpus_snapshot.verify("http://q", "wikipedia", 87173) == 0
    assert "87173 points" in capsys.readouterr().out


def test_a_different_corpus_is_refused(served, capsys):
    served(_info(points=42))
    assert corpus_snapshot.verify("http://q", "wikipedia", 87173) == 1
    assert "expected 87173" in capsys.readouterr().err


def test_a_collection_still_indexing_is_refused(served):
    served(_info(status="yellow"))
    assert corpus_snapshot.verify("http://q", "wikipedia", 87173) == 1


def test_the_wrong_vector_size_is_refused(served):
    # bge-small is 384-d; a 768-d collection is a different embedder entirely.
    served(_info(size=768))
    assert corpus_snapshot.verify("http://q", "wikipedia", 87173) == 1


def test_restore_without_a_source_is_an_error_not_a_silent_start():
    assert corpus_snapshot.main(["restore", "--from-url", ""]) == 2


# --- the bill watchdog --------------------------------------------------------

def _rows(*pairs):
    return [{"description": name, "cost": cost} for name, cost in pairs]


def test_spending_under_the_ceiling_passes():
    verdict = judge(_rows(("rag-wikipedia-inference", "0.21")), Decimal("1.00"))
    assert not verdict.stop
    assert verdict.apps_usd == Decimal("0.21")


def test_spending_over_the_ceiling_stops_the_apps():
    verdict = judge(_rows(("rag-wikipedia-inference", "1.40")), Decimal("1.00"))
    assert verdict.stop
    assert "stopping the apps" in verdict.markdown()


def test_the_ceiling_is_the_whole_workspace_not_just_this_project():
    # Modal's bill cannot tell our apps from anything else running in the
    # workspace, so the ceiling is read against the total.
    verdict = judge(_rows(("something-else", "2.00"), ("rag-wikipedia-worker", "0.10")),
                    Decimal("1.00"))
    assert verdict.stop
    assert verdict.apps_usd == Decimal("0.10")
    assert verdict.total_usd == Decimal("2.10")


def test_an_empty_bill_is_a_quiet_pass():
    verdict = judge([], DEFAULT_CEILING_USD)
    assert not verdict.stop and verdict.total_usd == 0


def test_costs_are_decimal_not_float():
    # 0.1 + 0.2 must not be 0.30000000000000004 when it is compared with a ceiling.
    verdict = judge(_rows(("rag-wikipedia-inference", "0.1"), ("rag-wikipedia-worker", "0.2")),
                    Decimal("0.3"))
    assert verdict.total_usd == Decimal("0.3")
    assert not verdict.stop


def test_the_report_is_markdown_a_ci_summary_can_print(tmp_path):
    report = tmp_path / "today.json"
    report.write_text(json.dumps(_rows(("rag-wikipedia-inference", "0.05"))), encoding="utf-8")
    from watchdog import main

    assert main(["watchdog.py", str(report)]) == 0
