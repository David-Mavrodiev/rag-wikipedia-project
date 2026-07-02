from unittest.mock import MagicMock


def test_token_budget_truncation():
    from app.core.retrieval import retrieve

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384

    store = MagicMock()
    store.search.return_value = [
        {"score": 0.9, "text": "word " * 1000, "title": "A", "source_id": "1"},
        {"score": 0.85, "text": "word " * 1000, "title": "B", "source_id": "2"},
        {"score": 0.8, "text": "word " * 1000, "title": "C", "source_id": "3"},
    ]

    chunks, empty = retrieve("test query", embedder, store, top_k=3, token_budget=500)
    assert len(chunks) <= 2
    assert not empty


def test_returns_empty_when_no_results():
    from app.core.retrieval import retrieve

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = []

    chunks, empty = retrieve("test query", embedder, store)
    assert chunks == []
    assert empty is True


def test_refusal_on_low_score():
    from app.core.retrieval import retrieve

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = [
        {"score": 0.01, "text": "some text", "title": "X", "source_id": "1"},
    ]

    _, empty = retrieve("test query", embedder, store)
    assert empty is True
