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


# --- end-to-end refusal: retrieval accepting is NOT the same as answering ----


class RefusingLLM:
    """Retrieval finds evidence, the model declines to use it (the gravity case)."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "I don't know based on the provided context."


def test_model_refusal_is_recorded_end_to_end(monkeypatch):
    # THE finding: decide_evidence accepts, the model refuses. The report must
    # show both layers instead of only the retrieval verdict.
    _patch_retrieve(monkeypatch)

    report = run_eval.evaluate_golden(
        _complete_golden_set(), object(), object(), RefusingLLM(), k=5
    )

    answerable = [c for c in report["cases"] if not c["expected_refusal"]]
    assert all(c["refused"] is False for c in answerable)            # retrieval accepted
    assert all(c["answer_refused"] is True for c in answerable)      # model declined
    assert all(c["end_to_end_refused"] is True for c in answerable)  # user saw a refusal

    # and the aggregate makes the gap visible
    assert report["answerable_refusal_rate"] == 0.0        # retrieval view: perfect
    assert report["e2e_answerable_refusal_rate"] == 1.0    # reality: refused every one


def test_answering_model_is_not_marked_refused(monkeypatch):
    _patch_retrieve(monkeypatch)

    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), FakeLLM(), k=5)

    answerable = [c for c in report["cases"] if not c["expected_refusal"]]
    assert all(c["end_to_end_refused"] is False for c in answerable)
    assert report["e2e_answerable_refusal_rate"] == 0.0


def test_fast_path_omits_every_e2e_key(monkeypatch):
    # No llm -> the keys must be ABSENT, not 0.0. "Not measured" must never read
    # as "never refuses".
    _patch_retrieve(monkeypatch)

    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), k=5)

    for key in ("e2e_refusal_accuracy", "e2e_answerable_refusal_rate", "e2e_false_accept_rate"):
        assert key not in report
    assert all("answer_refused" not in c for c in report["cases"])
    assert all("end_to_end_refused" not in c for c in report["cases"])


def test_unanswerable_refused_by_retrieval_costs_no_generation(monkeypatch):
    # When retrieval already refuses, the API short-circuits and never calls the
    # LLM. The eval must mirror that instead of burning a generation per case.
    _patch_retrieve(monkeypatch)

    llm = RefusingLLM()
    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), llm, k=5)

    # 15 answerable cases generate; the 5 unanswerable ones are refused by
    # retrieval, so they must not add any further prompts.
    assert len(llm.prompts) == 15
    assert report["e2e_refusal_accuracy"] == 1.0
    assert report["e2e_false_accept_rate"] == 0.0


# --- title scoring: recall and precision must agree on normalization --------

def _title_case_chunks() -> list[dict]:
    # Corpus title casing differs from the golden file's, which is normal:
    # golden.jsonl is hand-written, titles come from the dump.
    return [
        {"score": 0.95, "text": "Python is a programming language.", "title": "PYTHON"},
    ]


def test_precision_matches_recall_when_title_case_differs():
    # The bug: recall matched on a casefolded title while precision compared raw
    # strings, so this case scored recall 1.0 / precision 0.0 and dragged the
    # precision gate below 0.6 on a retrieval that had actually succeeded.
    score = run_eval.score_answerable_case(
        {"question": "Who created Python?", "expected_titles": ["Python"]},
        _title_case_chunks(),
        refused=False,
        k=1,
    )

    assert score["recall"] == 1.0
    assert score["precision"] == 1.0
    assert score["matched_titles"] == ["Python"]
    assert score["missing_titles"] == []


def test_title_scoring_ignores_surrounding_whitespace():
    score = run_eval.score_answerable_case(
        {"question": "Who created Python?", "expected_titles": ["Python"]},
        [{"score": 0.95, "text": "Python is a language.", "title": "  python  "}],
        refused=False,
        k=1,
    )

    assert score["recall"] == 1.0
    assert score["precision"] == 1.0


def test_unmatched_title_still_scores_zero():
    # The normalization must not turn a genuine miss into a match.
    score = run_eval.score_answerable_case(
        {"question": "Who created Python?", "expected_titles": ["Python"]},
        [{"score": 0.95, "text": "France is in Europe.", "title": "France"}],
        refused=False,
        k=1,
    )

    assert score["recall"] == 0.0
    assert score["precision"] == 0.0
    assert score["missing_titles"] == ["Python"]


def test_score_answerable_case_tolerates_zero_k_on_the_term_path():
    # k=0 used to divide by zero on the term path while the title path guarded.
    score = run_eval.score_answerable_case(
        {"question": "Who created Python?", "expected_sources": ["Python"]},
        _title_case_chunks(),
        refused=False,
        k=0,
    )

    assert score["precision"] == 0.0
