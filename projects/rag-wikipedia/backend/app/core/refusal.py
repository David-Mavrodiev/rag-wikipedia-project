from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.runtime_config import get_runtime_config

# Single source of truth for what "refused" means, shared by the API (which
# returns it verbatim on empty/low-score retrieval), the prompt (which instructs
# the model to emit it), and any client/UI that needs to tell a refusal apart
# from a real answer. Keeping it here stops those three from drifting.
REFUSAL_MESSAGE = "I don't know based on the provided context."

# Smart (curly) quotes -> ASCII. Model output is not byte-stable: the same
# refusal can come back wrapped in curly double quotes or written with a curly
# apostrophe, and none of that should change the verdict. Handling only the
# ASCII forms is how a refusal silently gets reported as a grounded answer.
_SMART_QUOTES = str.maketrans(
    {
        "“": '"',  # left double quotation mark
        "”": '"',  # right double quotation mark
        "‘": "'",  # left single quotation mark
        "’": "'",  # right single quotation mark (curly apostrophe)
    }
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "did",
    "do",
    "for",
    "had",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "who",
}

_PRIVATE_OR_TIME_DEPENDENT_PATTERNS = (
    r"\bmy\b",
    r"\bme\b",
    r"\bi\b",
    r"\btoday\b",
    r"\btomorrow\b",
    r"\byesterday\b",
    r"\bright now\b",
    r"\bcurrent\b",
    r"\bprivate\b",
    r"\bpassword\b",
    r"\bsecurity code\b",
)


@dataclass(frozen=True)
class EvidenceDecision:
    refused: bool
    reason: str
    top_score: float
    score_margin: float
    overlap_terms: list[str]


def query_terms(query: str) -> set[str]:
    terms = set()
    for token in re.findall(r"[a-zA-Z0-9]+", query.lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        terms.add(token)
    return terms


def evidence_overlap(query: str, chunks: list[dict]) -> list[str]:
    terms = query_terms(query)
    if not terms:
        return []

    combined = " ".join(chunk.get("text", "") for chunk in chunks[:3]).lower()
    return sorted(term for term in terms if term in combined)


def is_private_or_time_dependent(query: str) -> bool:
    normalized = query.lower()
    return any(re.search(pattern, normalized) for pattern in _PRIVATE_OR_TIME_DEPENDENT_PATTERNS)


def decide_evidence(query: str, results: list[dict]) -> EvidenceDecision:
    runtime = get_runtime_config()
    if is_private_or_time_dependent(query):
        return EvidenceDecision(True, "private_or_time_dependent_question", 0.0, 0.0, [])

    if not results:
        return EvidenceDecision(True, "no_results", 0.0, 0.0, [])

    top_score = float(results[0].get("score", 0.0))
    second_score = float(results[1].get("score", 0.0)) if len(results) > 1 else 0.0
    margin = top_score - second_score
    overlap = evidence_overlap(query, results)

    required_overlap = runtime.refusal_min_overlap_terms

    if top_score < max(settings.refusal_threshold, runtime.refusal_min_score):
        return EvidenceDecision(True, "score_below_minimum", top_score, margin, overlap)

    if len(overlap) >= required_overlap:
        return EvidenceDecision(False, "sufficient_overlap", top_score, margin, overlap)

    if (
        top_score >= runtime.refusal_high_confidence_score
        and margin >= runtime.refusal_min_margin
    ):
        return EvidenceDecision(False, "high_confidence_vector_match", top_score, margin, overlap)

    return EvidenceDecision(True, "insufficient_evidence_overlap", top_score, margin, overlap)


def is_refusal(answer: str) -> bool:
    """Return True when *answer* is a refusal, decided by the TEXT only.

    This deliberately does NOT look at whether citations were parsed. A grounded
    answer that happens to omit ``[n]`` markers (small local models do this
    intermittently) is a real answer, not a refusal — conflating "no citations"
    with "refused" is the exact bug this function exists to prevent.

    Covers both refusal kinds:
      * hard refusal — the API returns ``REFUSAL_MESSAGE`` verbatim when
        retrieval is empty or below the score threshold;
      * soft refusal — the model itself emits the refusal sentence, sometimes
        followed by a short explanation ("... The context only mentions X.").

    Tolerant to leading/trailing whitespace, wrapping quotes (ASCII *and*
    curly), case, and curly apostrophes, since model output is not byte-stable.
    """
    # Normalize smart quotes BEFORE stripping, so curly-wrapped answers get
    # their quotes removed too; then strip again for any inner whitespace.
    normalized = answer.strip().translate(_SMART_QUOTES).strip('"').strip().lower()
    return normalized.startswith(REFUSAL_MESSAGE.lower())
