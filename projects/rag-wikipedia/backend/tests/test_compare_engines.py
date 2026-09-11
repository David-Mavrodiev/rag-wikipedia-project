"""The comparison harness must survive a flaky model runner without lying.

A local runner is not reliable infrastructure - a real run died 25 minutes in
with "model runner has unexpectedly stopped". These cover the part that matters
when that happens: a transient failure is retried, a persistent one is recorded
as an error rather than scored as a refusal, and the report says it is
incomplete instead of publishing a rate over the cases that happened to survive.
"""

from __future__ import annotations

import pytest
from engines.base import EngineResult
from eval import compare_engines
from eval.compare_engines import build_markdown, score_case, score_suite


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(compare_engines, "RETRY_PAUSE_S", 0.0)


class _Engine:
    """Fails its first *failures* calls, then answers."""

    def __init__(self, failures: int = 0, refused: bool = False):
        self.failures = failures
        self.refused = refused
        self.calls = 0

    def answer(self, question: str):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("model runner has unexpectedly stopped")
        return EngineResult(
            answer="an answer [1].",
            citations=[{"index": 1}],
            refused=self.refused,
            refusal_reason="no_evidence" if self.refused else None,
            stats={"llm_calls": 1, "retrieved": 1},
        )


CASE = {"id": "c-1", "question": "What is Python?", "expected_refusal": False}


def test_a_transient_failure_is_retried():
    engine = _Engine(failures=1)
    scored = score_case(engine, CASE)

    assert "error" not in scored
    assert scored["refused"] is False
    assert engine.calls == 2


def test_a_persistent_failure_is_recorded_not_scored():
    # The distinction that matters: scoring a crash as a refusal would credit
    # an engine for dying on an unanswerable question.
    scored = score_case(_Engine(failures=99), CASE)

    assert "model runner has unexpectedly stopped" in scored["error"]
    assert "refused" not in scored


def test_errored_cases_are_excluded_and_counted():
    suite = score_suite(_Engine(failures=99), [CASE, dict(CASE, id="c-2")])

    assert suite["n_errors"] == 2
    assert suite["complete"] is False
    assert suite["n_answerable"] == 0
    assert suite["mean_llm_calls"] == 0.0


def test_a_clean_suite_reports_itself_complete():
    suite = score_suite(_Engine(), [CASE])

    assert suite["n_errors"] == 0
    assert suite["complete"] is True
    assert suite["n_answerable"] == 1


def test_an_incomplete_report_says_so_at_the_top():
    report = {
        "collection": "wikipedia_eval",
        "llm_model": "llama3.2:3b",
        "embed_model": "bge",
        "suites": ["holdout"],
        "engines": {"direct": {"holdout": score_suite(_Engine(failures=99), [CASE])}},
    }
    markdown = build_markdown(report)

    assert "INCOMPLETE - not a measurement" in markdown
    assert markdown.index("INCOMPLETE") < markdown.index("false_accept_rate")


KEPT_PAIR = {
    "kept": True,
    "n_errors": 0,
    "complete": True,
    "n_answerable": 1,
    "n_unanswerable": 1,
    "false_accept_rate": 0.5,
    "refusal_accuracy": 0.5,
    "answerable_refusal_rate": 0.0,
    "mean_llm_calls": 1.0,
    "mean_latency_ms": 10.0,
    "cases": [],
}


def test_resume_keeps_pairs_already_scored(tmp_path, monkeypatch):
    """The point of saving after every pair: finishing a run that died.

    Without --resume, re-running the one suite that was missing would overwrite
    the three that survived - which is the exact loss the incremental save was
    added to prevent.
    """
    out = tmp_path / "cmp"
    out.with_suffix(".json").write_text(
        compare_engines.json.dumps(
            {
                "collection": "wikipedia_eval",
                "suites": ["holdout"],
                "engines": {"direct": {"holdout": KEPT_PAIR}},
            }
        ),
        encoding="utf-8",
    )

    built = []

    def _never_scored(engine, cases):
        built.append(cases)
        raise AssertionError("a kept pair must not be re-scored")

    monkeypatch.setattr(compare_engines, "BGEEmbedder", lambda **kw: object())
    monkeypatch.setattr(compare_engines, "QdrantStore", lambda **kw: object())
    monkeypatch.setattr(compare_engines, "OllamaLLM", lambda **kw: object())
    monkeypatch.setattr(compare_engines, "make_engine", lambda *a, **kw: object())
    monkeypatch.setattr(compare_engines, "score_suite", _never_scored)

    compare_engines.main(
        ["--suite", "holdout", "--engines", "direct", "--out", str(out), "--resume"]
    )

    saved = compare_engines.json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["engines"]["direct"]["holdout"]["kept"] is True
    assert built == []
