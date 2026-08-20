from eval import run_eval


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "Python is a programming language."


def _complete_golden_set() -> list[dict]:
    answerable = [
        {"question": f"answerable {index}", "expected_sources": ["Python"]}
        for index in range(15)
    ]
    unanswerable = [
        {"question": f"unanswerable {index}", "expected_refusal": True}
        for index in range(5)
    ]
    return answerable + unanswerable


def _patch_retrieve(monkeypatch):
    def fake_retrieve(query, embedder, store, top_k):
        if query.startswith("answerable"):
            return [
                {
                    "score": 0.95,
                    "text": "Python is a programming language.",
                    "title": "Python",
                    "source_id": "python",
                }
            ], False
        return [], True

    monkeypatch.setattr("app.core.retrieval.retrieve", fake_retrieve)


def test_evaluate_golden_reports_groundedness(monkeypatch):
    _patch_retrieve(monkeypatch)

    llm = FakeLLM()
    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), llm, k=5)

    assert report["recall@5"] == 1.0
    assert report["mrr"] == 1.0
    assert report["groundedness"] == 1.0
    assert report["refusal_accuracy"] == 1.0
    assert report["n_answerable"] == 15
    assert report["n_unanswerable"] == 5
    assert len(report["cases"]) == 20
    assert report["cases"][0]["matched_sources"] == ["Python"]
    assert len(llm.prompts) == 15


def test_fast_path_skips_the_llm_entirely(monkeypatch):
    # THE point of the flag: no llm -> no generation, so `make eval` stays a
    # seconds-long retrieval-only run with no dependency on the LLM.
    _patch_retrieve(monkeypatch)

    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), k=5)

    assert report["recall@5"] == 1.0
    assert report["mrr"] == 1.0
    assert report["refusal_accuracy"] == 1.0
    # Omitted, NOT 0.0 — "not measured" must not read as "badly grounded".
    assert len(report["cases"]) == 20
    assert "groundedness" not in report


def test_fast_path_never_calls_the_llm(monkeypatch):
    _patch_retrieve(monkeypatch)

    llm = FakeLLM()
    run_eval.evaluate_golden(_complete_golden_set(), object(), object(), None, k=5)

    assert llm.prompts == []


def test_flag_defaults_to_off():
    assert run_eval.parse_args([]).with_groundedness is False
    assert run_eval.parse_args(["--with-groundedness"]).with_groundedness is True


def test_groundedness_is_report_only_not_a_gate():
    report = {
        "recall@5": 1.0,
        "mrr": 1.0,
        "precision@5": 1.0,
        "groundedness": 0.0,
        "refusal_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "answerable_refusal_rate": 0.0,
        "n_answerable": 15,
        "n_unanswerable": 5,
    }

    assert run_eval.gate_failures(report, k=5) == []


def test_gate_still_works_without_groundedness_key():
    # The fast-path report has no groundedness key; gating must not KeyError.
    report = {
        "recall@5": 1.0,
        "mrr": 1.0,
        "precision@5": 1.0,
        "refusal_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "answerable_refusal_rate": 0.0,
        "n_answerable": 15,
        "n_unanswerable": 5,
    }

    assert run_eval.gate_failures(report, k=5) == []


def test_markdown_report_includes_failures(tmp_path):
    report = {
        "recall@5": 0.5,
        "mrr": 0.5,
        "precision@5": 0.5,
        "refusal_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "answerable_refusal_rate": 0.0,
        "n_answerable": 1,
        "n_unanswerable": 1,
        "cases": [
            {
                "question": "Who created Python?",
                "expected_refusal": False,
                "refused": False,
                "recall": 0.0,
                "reciprocal_rank": 0.0,
                "matched_sources": [],
                "missing_sources": ["Guido van Rossum"],
                "evidence_reason": "sufficient_overlap",
                "top_score": 0.9,
                "score_margin": 0.1,
                "overlap_terms": ["python"],
                "retrieved": [{"title": "Python"}],
            },
            {
                "question": "What did I have for breakfast?",
                "expected_refusal": True,
                "refused": True,
                "evidence_reason": "insufficient_evidence_overlap",
                "top_score": 0.4,
                "score_margin": 0.0,
                "overlap_terms": [],
                "retrieved": [],
            },
        ],
    }
    path = tmp_path / "report.md"

    run_eval.write_markdown_report(report, path, k=5)

    text = path.read_text()
    assert "FAIL: Who created Python?" in text
    assert "missing_sources: Guido van Rossum" in text


def test_answerable_refusal_counts_as_metric_failure(monkeypatch):
    def fake_retrieve(query, embedder, store, top_k):
        return [
            {
                "score": 0.95,
                "text": "Python is a programming language.",
                "title": "Python",
                "source_id": "python",
            }
        ], True

    monkeypatch.setattr("app.core.retrieval.retrieve", fake_retrieve)

    golden = [
        {"question": "What is Python?", "expected_sources": ["Python"]},
        *[
            {"question": f"unanswerable {index}", "expected_refusal": True}
            for index in range(19)
        ],
    ]
    report = run_eval.evaluate_golden(golden, object(), object(), k=5)

    assert report["recall@5"] == 0.0
    assert report["answerable_refusal_rate"] == 1.0
