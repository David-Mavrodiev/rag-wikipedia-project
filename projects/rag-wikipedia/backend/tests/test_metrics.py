from eval.metrics import groundedness, mrr, recall_at_k, reciprocal_rank


def test_recall_at_k_full_match():
    texts = ["Python is a language", "ML is AI"]
    assert recall_at_k(texts, ["Python", "language"], k=2) == 1.0


def test_recall_at_k_partial():
    texts = ["Python is a language", "unrelated text"]
    score = recall_at_k(texts, ["Python", "Java"], k=2)
    assert 0.0 < score < 1.0


def test_recall_at_k_no_match():
    texts = ["unrelated text"]
    assert recall_at_k(texts, ["Python"], k=1) == 0.0


def test_recall_at_k_empty_keywords():
    assert recall_at_k(["text"], [], k=1) == 0.0


def test_reciprocal_rank_first():
    texts = ["Python is a language", "unrelated"]
    assert reciprocal_rank(texts, ["Python"]) == 1.0


def test_reciprocal_rank_second():
    texts = ["unrelated", "Python is a language"]
    assert reciprocal_rank(texts, ["Python"]) == 0.5


def test_reciprocal_rank_none():
    texts = ["unrelated"]
    assert reciprocal_rank(texts, ["Python"]) == 0.0


def test_mrr_calculation():
    scores = [1.0, 0.5, 0.0]
    result = mrr(scores)
    assert abs(result - (1.0 + 0.5 + 0.0) / 3) < 1e-9


def test_mrr_empty():
    assert mrr([]) == 0.0


def test_groundedness_full():
    answer = "Python language"
    context = ["Python is a language"]
    score = groundedness(answer, context)
    assert score > 0.5


def test_groundedness_empty_answer():
    assert groundedness("", ["some context"]) == 0.0


def test_groundedness_empty_context():
    assert groundedness("some answer", []) == 0.0
