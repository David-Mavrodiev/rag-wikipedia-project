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


def test_refusal_on_high_score_without_evidence_overlap(monkeypatch):
    from dataclasses import replace

    from app.core import runtime_config
    from app.core.refusal import decide_evidence
    from app.core.retrieval import retrieve

    # Two bugs used to make this test pass for the wrong reasons: the old query
    # ("Where did I leave my keys yesterday?") short-circuited on the
    # private/time-dependent check and never reached the overlap logic at all,
    # and decide_evidence reads the RUNTIME config, so patching `settings` left
    # the real 0.78 high-confidence threshold in place. Both are pinned below.
    strict = replace(runtime_config.get_runtime_config(), refusal_high_confidence_score=0.95)
    monkeypatch.setattr(runtime_config, "_active_config", strict)

    results = [
        {
            "score": 0.9,
            "text": "France is a country in Europe.",
            "title": "France",
            "source_id": "1",
        },
        {"score": 0.88, "text": "Paris is a city.", "title": "Paris", "source_id": "2"},
    ]

    # Answerable-looking question, high vector score, zero term overlap with the
    # evidence: the branch under test.
    query = "What is photosynthesis?"
    decision = decide_evidence(query, results)
    assert decision.refused is True
    assert decision.reason == "insufficient_evidence_overlap"
    assert decision.overlap_terms == []

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = results

    _, empty = retrieve(query, embedder, store)

    assert empty is True


def test_allows_high_score_with_evidence_overlap():
    from app.core.retrieval import retrieve

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = [
        {
            "score": 0.9,
            "text": "Python was created by Guido van Rossum.",
            "title": "Python",
            "source_id": "1",
        }
    ]

    _, empty = retrieve("Who created Python?", embedder, store)

    assert empty is False


def test_private_question_refuses_before_embedding():
    from app.core.retrieval import retrieve

    embedder = MagicMock()
    store = MagicMock()

    chunks, empty = retrieve("What is my current bank balance?", embedder, store)

    assert chunks == []
    assert empty is True
    embedder.embed.assert_not_called()
    store.search.assert_not_called()
