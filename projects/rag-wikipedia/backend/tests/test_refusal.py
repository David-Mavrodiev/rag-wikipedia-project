"""Refusal classification — a credibility-critical path.

Guards the bug where a grounded answer that omits [n] citation markers was
mislabeled as a refusal. Refusal must be decided by the answer TEXT, never by
citation count. If this behavior regresses, these tests fail immediately.
"""

from unittest.mock import patch

import pytest
from app.core.refusal import (
    REFUSAL_MESSAGE,
    evidence_overlap,
    is_private_or_time_dependent,
    is_refusal,
)

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


def test_refusal_tolerates_curly_double_quotes():
    # Regression: curly wrapping quotes used to defeat strip('"'), so a refusal
    # was reported as a grounded answer.
    assert is_refusal("“I don't know based on the provided context.”") is True


def test_refusal_tolerates_curly_quotes_and_apostrophe_together():
    assert is_refusal("“I don’t know based on the provided context.”") is True


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


# --- intent classifier: answerable questions must NOT be short-circuited ------
#
# `is_private_or_time_dependent` refuses BEFORE embedding, so a false positive
# here is a hard refusal of a question the corpus can answer — invisible to the
# eval suites, because every unanswerable case in them contains "my".

@pytest.mark.parametrize(
    "question",
    [
        "Who won World War I?",          # Roman numeral, not the pronoun "I"
        "What is World War I about?",
        "What is alternating current?",  # physics noun, not "current <thing>"
        "What is an ocean current?",
        "What is the current of a circuit?",
        "What is ME/CFS?",               # abbreviation, not the pronoun "me"
        "Who was Aristotle?",
        "What is the largest planet?",
    ],
)
def test_answerable_questions_are_not_classified_private(question):
    assert is_private_or_time_dependent(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "What did I have for breakfast this morning?",
        "Who called me from Alaska today?",
        "What is my current bank balance?",
        "Who is the current president?",
        "Where is my passport currently located?",
        "What is the security code for my apartment building?",
        "Which restaurant will I visit tomorrow?",
        "What is happening right now?",
    ],
)
def test_private_or_time_dependent_questions_are_classified(question):
    assert is_private_or_time_dependent(question) is True


# --- evidence overlap: whole tokens, never substrings ------------------------

def test_overlap_ignores_substring_matches():
    # "art" inside "particles"/"part" is not evidence about art. Counting it
    # inflated the overlap and turned a refusal into a false accept.
    assert evidence_overlap("What is art?", [{"text": "Particles are part of nature."}]) == []


def test_overlap_ignores_concatenated_substrings():
    chunks = [{"text": "A catalog of concatenated strings."}]
    assert evidence_overlap("What is a cat?", chunks) == []


def test_overlap_matches_whole_tokens():
    chunks = [{"text": "The cat is a domestic species."}]
    assert evidence_overlap("What is a cat?", chunks) == ["cat"]


def test_overlap_is_case_insensitive():
    chunks = [{"text": "PYTHON was created by Guido van Rossum."}]
    assert "python" in evidence_overlap("Who created Python?", chunks)


# --- "private" / "password" are encyclopedia subjects too ---------------------

@pytest.mark.parametrize(
    "question",
    [
        "What is a private company?",
        "What is private equity?",
        "What is private property?",
        "What is private international law?",
        "What is a private university?",
        "What is a private key?",
        "What is password hashing?",
        "What is a password manager?",
        "How does password authentication work?",
    ],
)
def test_private_and_password_as_subjects_are_answerable(question):
    assert is_private_or_time_dependent(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "What is your private phone number?",
        "What is John's private salary?",
        "What is her password?",
        "What is my manager's private salary?",
        "What is my manager\u2019s private salary?",  # curly apostrophe
        "What is the password for my laptop?",
        "What is my private recovery phrase?",
        "What is the security code for my apartment building?",
    ],
)
def test_someones_private_information_is_refused(question):
    assert is_private_or_time_dependent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "Tell me about Apollo.",
        "Tell me about Aristotle.",
        "Show me the history of Alaska.",
        "Give me a summary of Anarchism.",
        "Find me the definition of ASCII.",
    ],
)
def test_tell_me_about_a_topic_is_answerable(question):
    # "Tell me about X" is an ordinary encyclopedia query. Treating the bare
    # word "me" as personal intent refused the corpus's own articles -
    # "Tell me about Apollo." is adversarial case adv-004, expected ANSWERABLE.
    assert is_private_or_time_dependent(question) is False


def test_me_still_refused_when_the_question_is_actually_personal():
    assert is_private_or_time_dependent("What did my manager say about me in private?") is True
