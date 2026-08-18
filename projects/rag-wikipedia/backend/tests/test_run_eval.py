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


def test_evaluate_golden_reports_groundedness(monkeypatch):
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

    llm = FakeLLM()
    report = run_eval.evaluate_golden(_complete_golden_set(), object(), object(), llm, k=5)

    assert report["recall@5"] == 1.0
    assert report["mrr"] == 1.0
    assert report["groundedness"] == 1.0
    assert report["refusal_accuracy"] == 1.0
    assert report["n_answerable"] == 15
    assert report["n_unanswerable"] == 5
    assert len(llm.prompts) == 15


def test_groundedness_is_report_only_not_a_gate():
    report = {
        "recall@5": 1.0,
        "mrr": 1.0,
        "groundedness": 0.0,
        "refusal_accuracy": 1.0,
        "n_answerable": 15,
        "n_unanswerable": 5,
    }

    assert run_eval.gate_failures(report, k=5) == []

