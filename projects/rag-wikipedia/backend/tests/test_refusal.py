"""Refusal classification — a credibility-critical path.

Guards the bug where a grounded answer that omits [n] citation markers was
mislabeled as a refusal. Refusal must be decided by the answer TEXT, never by
citation count. If this behavior regresses, these tests fail immediately.
"""

from unittest.mock import patch

from app.core.refusal import REFUSAL_MESSAGE, is_refusal


# --- pure classifier -------------------------------------------------------

def test_exact_refusal_is_refusal():
    assert is_refusal(REFUSAL_MESSAGE) is True


def test_soft_refusal_with_trailing_explanation():
    # The model sometimes appends context after the refusal sentence.
    answer = REFUSAL_MESSAGE + " The context only mentions Andre Agassi."
    assert is_refusal(answer) is True


def test_grounded_answer_without_markers_is_not_refusal():
    # THE regression: a real answer with zero [n] markers is NOT a refusal.
    answer = "Abraham Lincoln was the 16th president of the United States."
    assert is_refusal(answer) is False


def test_grounded_answer_with_markers_is_not_refusal():
    answer = "Abraham Lincoln was the 16th president [1]. He led the Union [2]."
    assert is_refusal(answer) is False


def test_refusal_tolerates_whitespace_quotes_and_case():
    assert is_refusal('  "I DON\'T know based on the provided context."  ') is True


def test_refusal_tolerates_curly_apostrophe():
    assert is_refusal("I don’t know based on the provided context.") is True


def test_empty_answer_is_not_refusal():
    assert is_refusal("") is False


# --- API wiring (does /query set `refused` correctly?) ---------------------

_ABOVE_THRESHOLD = [
    {"score": 0.95, "text": "Abraham Lincoln was the 16th president.",
     "title": "Abraham Lincoln", "source_id": "307"},
]


def _post(client, question, generated):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = _ABOVE_THRESHOLD
        mock_llm.return_value.generate.return_value = generated
        return client.post("/query", json={"question": question}).json()


def test_api_grounded_without_markers_is_not_refused(client):
    data = _post(client, "Who was Abraham Lincoln?",
                 "Abraham Lincoln was the 16th president of the United States.")
    assert data["refused"] is False          # <-- the fix: no longer a refusal
    assert data["citations"] == []           # no markers -> no citations, but answered


def test_api_grounded_with_markers_has_citations(client):
    data = _post(client, "Who was Abraham Lincoln?",
                 "Abraham Lincoln was the 16th president [1].")
    assert data["refused"] is False
    assert len(data["citations"]) == 1


def test_api_soft_refusal_from_llm_is_refused(client):
    data = _post(client, "Who won the 2022 World Cup?", REFUSAL_MESSAGE)
    assert data["refused"] is True
    assert data["citations"] == []


def test_api_empty_retrieval_is_hard_refusal(client):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm"),
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = []
        data = client.post("/query", json={"question": "anything"}).json()
    assert data["refused"] is True
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []
