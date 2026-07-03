from __future__ import annotations


def recall_at_k(retrieved_texts: list[str], expected_keywords: list[str], k: int) -> float:
    """Fraction of expected keywords found in top-k retrieved texts."""
    top_k = retrieved_texts[:k]
    combined = " ".join(top_k).lower()
    hits = sum(1 for keyword in expected_keywords if keyword.lower() in combined)
    return hits / len(expected_keywords) if expected_keywords else 0.0


def reciprocal_rank(retrieved_texts: list[str], expected_keywords: list[str]) -> float:
    """Reciprocal rank of first chunk containing any expected keyword."""
    for index, text in enumerate(retrieved_texts, 1):
        if any(keyword.lower() in text.lower() for keyword in expected_keywords):
            return 1.0 / index
    return 0.0


def mrr(scores: list[float]) -> float:
    """Mean reciprocal rank over a list of reciprocal rank scores."""
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def refusal_accuracy(refused_flags: list[bool]) -> float:
    """Fraction of unanswerable questions that were correctly refused."""
    if not refused_flags:
        return 0.0
    return sum(refused_flags) / len(refused_flags)


def groundedness(answer: str, context_chunks: list[str]) -> float:
    """Simple lexical groundedness: fraction of answer tokens found in context."""
    if not context_chunks or not answer.strip():
        return 0.0
    answer_tokens = set(answer.lower().split())
    context_text = " ".join(context_chunks).lower()
    context_tokens = set(context_text.split())
    overlap = answer_tokens & context_tokens
    return len(overlap) / len(answer_tokens) if answer_tokens else 0.0
