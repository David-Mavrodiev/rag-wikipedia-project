"""The end-to-end runner: what survives a stopped run, and what gets counted.

An end-to-end run is an hour of paced generation on the reference laptop, and it
has been stopped by heat before. These cover the properties that make such a run
worth its hour: every case is on disk the moment it finishes, a resume never
double-counts, an infrastructure error is not scored as a refusal, and the
report says when it is incomplete.
"""

from __future__ import annotations

import json

import pytest
from app.core.refusal import REFUSAL_MESSAGE
from engines.base import EngineResult
from eval import run_e2e
from eval.run_e2e import (
    build_markdown,
    generate,
    judge_records,
    latency_summary,
    load_raw,
    reconcile,
    run_case,
    score,
)
from eval.suites import SuiteProfileMismatch

CHUNK = {"title": "Biotite", "source_id": "b1", "text": "Biotite is a mica mineral."}


class _Engine:
    def __init__(self, answers: dict[str, EngineResult], fail: set[str] = frozenset()):
        self.answers = answers
        self.fail = set(fail)
        self.calls: list[str] = []

    def answer(self, question: str) -> EngineResult:
        self.calls.append(question)
        if question in self.fail:
            raise RuntimeError("model runner has unexpectedly stopped")
        return self.answers[question]


def _answered(text="Biotite is a mica mineral [1].") -> EngineResult:
    return EngineResult(answer=text, citations=[{"title": "Biotite"}], refused=False,
                        stats={"llm_calls": 1}, context=[CHUNK])


def _soft_refusal() -> EngineResult:
    return EngineResult(answer=REFUSAL_MESSAGE, refused=True, refusal_reason="soft_refusal",
                        stats={"llm_calls": 1}, context=[CHUNK])


def _hard_refusal() -> EngineResult:
    return EngineResult(answer=REFUSAL_MESSAGE, refused=True, refusal_reason="no_evidence",
                        stats={"llm_calls": 0})


def _case(cid, question, refusal=False, titles=("Biotite",)):
    return {"id": cid, "question": question, "expected_refusal": refusal,
            "expected_titles": [] if refusal else list(titles)}


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(run_e2e, "RETRY_PAUSE_S", 0.0)


# --- one case -----------------------------------------------------------------

def test_a_case_records_the_answer_and_the_context_it_came_from():
    engine = _Engine({"q": _answered()})
    record = run_case(engine, _case("a", "q"), sampler=None, cooldown_s=0.0)
    assert record["answer"].startswith("Biotite")
    assert record["context"] == [CHUNK]
    assert record["llm_calls"] == 1
    assert record["gpu"] is None


def test_a_persistent_failure_is_an_error_not_a_refusal():
    engine = _Engine({}, fail={"q"})
    record = run_case(engine, _case("a", "q"), sampler=None, cooldown_s=0.0, sleep=lambda s: None)
    assert "error" in record and "refused" not in record
    assert len(engine.calls) == run_e2e.MAX_ATTEMPTS


# --- checkpointing and resume -------------------------------------------------

def test_every_case_is_on_disk_as_it_finishes(tmp_path):
    path = tmp_path / "raw" / "s.direct.jsonl"
    engine = _Engine({"q1": _answered(), "q2": _soft_refusal()})
    generate(engine, [_case("a", "q1"), _case("b", "q2")], path, pacer=None, sampler=None,
             resume=False)
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == ["a", "b"]


def test_resume_skips_finished_cases_and_retries_errors(tmp_path):
    path = tmp_path / "raw" / "s.direct.jsonl"
    cases = [_case("a", "q1"), _case("b", "q2")]
    generate(_Engine({"q1": _answered()}, fail={"q2"}), cases, path, pacer=None, sampler=None,
             resume=False)

    second = _Engine({"q1": _answered(), "q2": _answered()})
    generate(second, cases, path, pacer=None, sampler=None, resume=True)

    assert second.calls == ["q2"]
    records = load_raw(path)
    assert [r["id"] for r in records] == ["a", "b"]
    assert all("error" not in r for r in records)


def test_a_fresh_run_does_not_append_to_an_old_one(tmp_path):
    path = tmp_path / "raw" / "s.direct.jsonl"
    cases = [_case("a", "q1")]
    for _ in range(2):
        generate(_Engine({"q1": _answered()}), cases, path, pacer=None, sampler=None,
                 resume=False)
    assert len(path.read_text().splitlines()) == 1


def test_an_edited_question_is_regenerated_not_kept_under_its_id(tmp_path):
    # Found in review: resuming by id would have filed the answer to the OLD
    # question under the edited one.
    path = tmp_path / "raw" / "s.direct.jsonl"
    generate(_Engine({"old wording": _answered()}), [_case("a", "old wording")], path,
             pacer=None, sampler=None, resume=False)

    engine = _Engine({"new wording": _answered()})
    generate(engine, [_case("a", "new wording")], path, pacer=None, sampler=None, resume=True)
    assert engine.calls == ["new wording"]


def test_a_stale_answer_is_dropped_and_expectations_come_from_the_suite():
    engine = _Engine({"kept": _answered(), "gone": _answered()})
    records = [run_case(engine, _case("old-id", q), sampler=None, cooldown_s=0.0)
               for q in ("kept", "gone")]
    suite = [{"id": "new-id", "question": "kept", "expected_refusal": True, "expected_titles": []}]

    aligned, stale = reconcile(records, suite)
    assert stale == 1
    assert [r["id"] for r in aligned] == ["new-id"]
    assert aligned[0]["expected_refusal"] is True  # corrected in the suite, applied here


def test_the_pacer_runs_before_every_generation(tmp_path):
    class _Pacer:
        calls = 0

        def before_call(self):
            self.calls += 1
            return 0.0

    pacer = _Pacer()
    generate(_Engine({"q1": _answered(), "q2": _answered()}),
             [_case("a", "q1"), _case("b", "q2")], tmp_path / "r.jsonl",
             pacer=pacer, sampler=None, resume=False)
    assert pacer.calls == 2


# --- scoring ------------------------------------------------------------------

def _records():
    engine = _Engine({
        "answered": _answered(),
        "declined": _soft_refusal(),
        "false accept": _answered(),
        "refused": _hard_refusal(),
    })
    cases = [
        _case("a1", "answered"),
        _case("a2", "declined"),
        _case("u1", "false accept", refusal=True),
        _case("u2", "refused", refusal=True),
    ]
    return [run_case(engine, c, sampler=None, cooldown_s=0.0) for c in cases], cases


def test_refusal_metrics_use_the_shared_definitions():
    records, cases = _records()
    metrics = score(records, cases)["metrics"]
    assert metrics["false_accept_rate"] == 0.5
    assert metrics["refusal_accuracy"] == 0.5
    assert metrics["answerable_refusal_rate"] == 0.5
    assert metrics["complete"] is True


def test_a_soft_refusal_with_the_article_in_context_is_counted():
    # "The model had the right article and declined it" is its own finding.
    records, cases = _records()
    metrics = score(records, cases)["metrics"]
    assert metrics["soft_refusals_answerable"] == 1
    assert metrics["soft_refusals_with_article_in_context"] == 1
    assert metrics["context_hit_rate"] == 1.0


def test_answer_quality_counts_only_answers():
    records, cases = _records()
    answers = score(records, cases)["answers"]
    assert answers["n_answered"] == 2  # the answered case and the false accept
    assert answers["citation_valid_rate"] == 1.0


def test_missing_and_errored_cases_make_the_report_incomplete():
    records, cases = _records()
    records[0] = {"id": "a1", "question": "answered", "expected_refusal": False, "error": "boom"}
    metrics = score(records[:3], cases)["metrics"]
    assert metrics["n_errors"] == 1
    assert metrics["n_missing"] == 1
    assert metrics["complete"] is False


def test_the_judge_only_reads_answers():
    records, _ = _records()

    class _Judge:
        pairs = 0

        def entailment(self, pairs):
            self.pairs += len(pairs)
            return [0.9] * len(pairs)

    judge = _Judge()
    judge_records(records, judge)
    judged = [r for r in records if "support" in r]
    assert {r["id"] for r in judged} == {"a1", "u1"}
    assert all(r["support"] == [0.9] for r in judged)


# --- latency ------------------------------------------------------------------

def test_latency_is_split_by_whether_the_model_ran_and_by_regime():
    records = [
        {"id": str(i), "llm_calls": 1, "latency_ms": 4000.0 + i, "gpu": {"regime": "full"}}
        for i in range(5)
    ] + [
        {"id": "t", "llm_calls": 1, "latency_ms": 20000.0, "gpu": {"regime": "throttled"}},
        {"id": "r", "llm_calls": 0, "latency_ms": 60.0, "gpu": None},
    ]
    summary = latency_summary(records)
    assert summary["generated"]["n"] == 6
    assert summary["generated_full"]["p50_ms"] == 4002.0
    # One throttled sample: a range, never a percentile it cannot support.
    assert summary["generated_throttled"]["p50_ms"] is None
    assert summary["generated_throttled"]["max_ms"] == 20000.0
    assert summary["no_generation"]["n"] == 1


# --- the report ---------------------------------------------------------------

def _report(records, cases):
    return {"suite": "s", "engine": "direct", "nli_model": None, "provenance": {},
            "conditions": {}, **score(records, cases),
            "cases": [run_e2e.compact_case(r) for r in records]}


def test_an_incomplete_run_says_so_at_the_top():
    records, cases = _records()
    markdown = build_markdown(_report(records[:2], cases))
    assert "INCOMPLETE - not a measurement" in markdown.splitlines()[1] + markdown.splitlines()[2]


def test_unsupported_claims_are_listed_by_case():
    records, cases = _records()
    for record in records:
        if not record["refused"]:
            record["claims"], record["support"] = ["Biotite is a mica mineral."], [0.1]
    markdown = build_markdown(_report(records, cases))
    assert "### Unsupported claims" in markdown
    assert "`a1` (0.10)" in markdown


# --- the corpus guard ---------------------------------------------------------

def test_a_suite_is_refused_against_the_wrong_corpus_before_anything_runs(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "profile", "fixture")
    with pytest.raises(SuiteProfileMismatch):
        run_e2e.main(["--suite", "serving_golden", "--no-nli"])
