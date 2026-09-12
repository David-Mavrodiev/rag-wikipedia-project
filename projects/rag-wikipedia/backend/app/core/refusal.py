from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.idf import load_table
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

# Questions no corpus can answer: they are about the ASKER, or about a fact that
# changes after ingestion. A match here short-circuits retrieval into a hard
# refusal, which makes an over-broad pattern far more expensive than a missing
# one. Bare `\bi\b` refused "Who won World War I?", `\bme\b` refused "What is
# ME/CFS?", and `\bcurrent\b` refused "What is alternating current?" — each word
# needs the neighbours that carry the personal or time-bound intent, not the
# word alone.
_PRIVATE_OR_TIME_DEPENDENT_PATTERNS = (
    # First person, possessive or reflexive. "my" is unambiguous by itself;
    # "i" is not (Roman numerals, abbreviations), so it matches only beside the
    # verb that makes the question personal.
    r"\bmy\b",
    r"\bmine\b",
    r"\bmyself\b",
    r"\b(?:did|do|have|had|can|could|should|will|would|am) i\b",
    # "me" is deliberately absent. "Tell me about Apollo", "Show me the history
    # of Alaska" and "Give me a summary of Anarchism" are ordinary ways to ask
    # an answerable question, and matching them refused the corpus's own
    # articles — "Tell me about Apollo." is adversarial case adv-004, expected
    # ANSWERABLE. Every genuinely personal question in the three suites is
    # already caught by "my"/"mine"/"myself" or a time word, so the "me" forms
    # carried no refusal case of their own; they only produced false refusals.
    # It went unnoticed because answerable_refusal_rate is reported but never
    # gated, so the suite stayed green while the demo refused real questions.
    # Time-dependent. "current" is an ordinary noun in physics, geography and
    # electronics, so only the adverb and the "current <changing thing>" forms
    # qualify.
    r"\btoday\b",
    r"\btomorrow\b",
    r"\byesterday\b",
    r"\bright now\b",
    r"\bcurrently\b",
    r"\bcurrent (?:president|prime minister|leader|ceo|champion|holder|price|"
    r"population|time|date|weather|score|version|balance|status|rate|value)\b",
    # Private or secret material. Both words are ordinary encyclopedia subjects
    # on their own — "private equity", "private international law", "private
    # key cryptography", "password hashing" — so matching the bare word refused
    # answerable questions. They qualify only once something marks the
    # information as a specific person's: `\bmy\b` above covers first person,
    # and these cover the rest. `['’]s` accepts both apostrophes because this
    # runs on raw query text, which is not normalized.
    r"\b(?:your|his|her|its|their|our)\s+(?:private|password)\b",
    r"\b\w+['’]s\s+(?:private|password)\b",
    r"\bpassword (?:for|to|at) (?:my|your|his|her|its|their|our)\b",
    r"\bprivate (?:recovery|seed) phrase\b",
    r"\bsecurity code\b",
)


@dataclass(frozen=True)
class EvidenceDecision:
    refused: bool
    reason: str
    top_score: float
    score_margin: float
    overlap_terms: list[str]
    # Defaulted so the four early-return paths above (private question, no
    # results, score below minimum) stay three-liners: none of them ever looked
    # at evidence, and giving them a fabricated coverage of 0.0 would read as
    # "measured and found nothing" instead of "not measured".
    coverage: float = 0.0
    uncovered_terms: list[str] = field(default_factory=list)


def tokenize_for_evidence(text: str) -> set[str]:
    """Split *text* into the same token shape `query_terms` produces.

    Both sides of the overlap test must be tokenized identically. A substring
    test ("art" in "particles", "cat" in "concatenated") reports evidence that
    is not there, inflates the overlap count, and turns a refusal into a false
    accept — the exact failure `decide_evidence` exists to catch.

    Public because `scripts/build_idf.py` MUST tokenize the corpus with this
    exact function. A document-frequency table built with a different splitter
    would key on terms the gate never looks up, and every lookup would miss and
    score 1.0 — turning the IDF gate into an accept-everything gate while still
    reporting numbers.
    """
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9]+", text)}


def query_terms(query: str) -> set[str]:
    terms = set()
    for token in tokenize_for_evidence(query):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        terms.add(token)
    return terms


# How many top chunks count as "the evidence". The count gate and the coverage
# gate MUST read the same window: scoring coverage over five chunks while the
# overlap list reports three would make a refusal's own diagnostics disagree
# with the decision that produced it.
_EVIDENCE_WINDOW = 3


def evidence_terms(chunks: list[dict]) -> set[str]:
    combined = " ".join(chunk.get("text", "") for chunk in chunks[:_EVIDENCE_WINDOW])
    return tokenize_for_evidence(combined)


def evidence_overlap(query: str, chunks: list[dict]) -> list[str]:
    terms = query_terms(query)
    if not terms:
        return []

    return sorted(terms & evidence_terms(chunks))


def is_private_or_time_dependent(query: str) -> bool:
    normalized = query.lower()
    return any(re.search(pattern, normalized) for pattern in _PRIVATE_OR_TIME_DEPENDENT_PATTERNS)


def decide_evidence(query: str, results: list[dict]) -> EvidenceDecision:
    """Decide whether *results* are evidence enough to answer *query*.

    The accept test is IDF-weighted coverage: what fraction of the question's
    specificity do the top chunks actually supply? The count-based predecessor
    (`len(overlap) >= refusal_min_overlap_terms`, default 1) accepted on any one
    shared token, which made the gate weaken as the corpus grew — every extra
    article is another chance for an unrelated chunk to contribute a common
    word. Coverage is immune to that: a common word carries almost no weight
    however many chunks hold it, so growth adds noise the gate already ignores.

    Falls back to the count gate when no IDF table has been built for the
    collection, so a fresh checkout degrades to the old behaviour instead of
    refusing everything.
    """
    runtime = get_runtime_config()
    if is_private_or_time_dependent(query):
        return EvidenceDecision(True, "private_or_time_dependent_question", 0.0, 0.0, [])

    if not results:
        return EvidenceDecision(True, "no_results", 0.0, 0.0, [])

    top_score = float(results[0].get("score", 0.0))
    second_score = float(results[1].get("score", 0.0)) if len(results) > 1 else 0.0
    margin = top_score - second_score

    if top_score < max(settings.refusal_threshold, runtime.refusal_min_score):
        return EvidenceDecision(
            True, "score_below_minimum", top_score, margin, evidence_overlap(query, results)
        )

    terms = query_terms(query)
    covered = terms & evidence_terms(results)
    overlap = sorted(covered)

    # The vector-similarity escape hatch, unchanged and deliberately checked
    # BEFORE the lexical test. A question phrased entirely unlike its source
    # ("What is the study of humankind?" -> Anthropology) can be a confident
    # embedding match with near-zero term overlap, and refusing it would trade
    # this gate's false accepts for false refusals on exactly the paraphrases a
    # dense retriever exists to handle.
    high_confidence = (
        top_score >= runtime.refusal_high_confidence_score
        and margin >= runtime.refusal_min_margin
    )

    # 0.0 means DISABLED, not "accept anything". Read as a plain threshold,
    # `coverage >= 0.0` is always true, so zero would accept every result that
    # cleared the score floor — WEAKER than the count gate it replaces, shipped
    # as an improvement. The threshold was measured on the serving corpus and
    # enabled on 2026-09-12 (the reasoning is in app.core.config). Zero is still
    # how the gate is turned off, and still what a collection with no IDF table
    # falls back to.
    table = load_table(settings.collection)
    if table is None or runtime.refusal_min_evidence_coverage <= 0.0:
        if len(overlap) >= runtime.refusal_min_overlap_terms:
            return EvidenceDecision(False, "sufficient_overlap", top_score, margin, overlap)
        if high_confidence:
            return EvidenceDecision(
                False, "high_confidence_vector_match", top_score, margin, overlap
            )
        return EvidenceDecision(
            True, "insufficient_evidence_overlap", top_score, margin, overlap
        )

    coverage = table.coverage(terms, covered)
    # What the question asked about that the evidence never mentions, rarest
    # first. This is the operator-facing half of a refusal: "insufficient
    # coverage 0.31" says a threshold was missed, `['brzezinski']` says why.
    uncovered = table.rarest_terms(terms - covered)

    if coverage >= runtime.refusal_min_evidence_coverage:
        return EvidenceDecision(
            False, "sufficient_evidence_coverage", top_score, margin, overlap, coverage, uncovered
        )

    if high_confidence:
        return EvidenceDecision(
            False, "high_confidence_vector_match", top_score, margin, overlap, coverage, uncovered
        )

    return EvidenceDecision(
        True, "insufficient_evidence_coverage", top_score, margin, overlap, coverage, uncovered
    )


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
