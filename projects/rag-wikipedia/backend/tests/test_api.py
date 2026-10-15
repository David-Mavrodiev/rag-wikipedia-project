from unittest.mock import patch

from app.core.refusal import REFUSAL_MESSAGE


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_query_empty_question(client):
    response = client.post("/query", json={"question": ""})
    assert response.status_code == 422


def test_query_too_long(client):
    response = client.post("/query", json={"question": "x" * 501})
    assert response.status_code == 422


def test_query_returns_refusal_when_no_context(client):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm"),
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = []
        response = client.post("/query", json={"question": "What is quantum gravity?"})

    assert response.status_code == 200
    data = response.json()
    # Assert the explicit state, not the old `refusal-text OR no-citations` OR,
    # which also passed for a grounded answer the model forgot to cite.
    assert data["refused"] is True
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []


def test_query_returns_answer_with_citations(client):
    mock_chunks = [
        {
            "score": 0.95,
            "text": "Python is a programming language [1].",
            "title": "Python",
            "source_id": "1",
        }
    ]
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = mock_chunks
        mock_llm.return_value.generate.return_value = "Python [1] is great."
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "citations" in data


def test_query_qdrant_down(client):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store"),
    ):
        mock_embedder.return_value.embed.side_effect = Exception("connection refused")
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 503


# --- cited: whether a reader can check the answer against a source ----------
def _answer_with(generated: str, client):
    chunk = {"score": 0.95, "text": "Python is a programming language.",
             "title": "Python", "source_id": "1"}
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = [chunk]
        mock_llm.return_value.generate.return_value = generated
        return client.post("/query", json={"question": "What is Python?"}).json()


def test_an_answer_citing_a_given_chunk_is_cited(client):
    data = _answer_with("Python is a programming language [1].", client)
    assert data["cited"] is True and data["refused"] is False


def test_a_placeholder_citation_is_served_but_marked_uncited(client):
    # What llama3.2:3b did with "Cite inline with [n] markers": served, and
    # indistinguishable from a sourced answer until this flag existed.
    data = _answer_with("Python is a programming language [n].", client)
    assert data["refused"] is False
    assert data["cited"] is False
    assert data["citations"] == []


def test_a_citation_past_the_context_is_not_a_source(client):
    data = _answer_with("Python is a programming language [7].", client)
    assert data["cited"] is False
