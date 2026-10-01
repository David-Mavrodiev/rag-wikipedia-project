"""Answer-level measurement: refusals are counted, never scored as answers.

The judge is faked everywhere here. What is under test is the bookkeeping that
decides WHICH answers are judged and how the verdicts aggregate - the part that
turned eight refusals into "poorly grounded answers" before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from eval.grounding import (
    NLIJudge,
    check_citations,
    claim_sentences,
    premise_windows,
    sentence_support,
    summarize_answers,
)

REFUSAL = "I don't know based on the provided context."


# --- citations ----------------------------------------------------------------

def test_a_citation_that_resolves_is_valid():
    check = check_citations("Lincoln was president [1].", n_context=3)
    assert check.valid and check.resolvable == (1,)


def test_a_literal_placeholder_is_not_a_citation():
    # What llama3.2:3b does with "Cite inline with [n] markers".
    check = check_citations("Biotite is a mica [n].", n_context=5)
    assert not check.valid
    assert check.placeholder


def test_a_citation_past_the_context_does_not_resolve():
    check = check_citations("Claim [7].", n_context=5)
    assert check.cited == (7,)
    assert not check.valid


# --- sentences and windows ----------------------------------------------------

def test_claim_sentences_strip_markers_and_split():
    answer = "Lincoln was the 16th president [1]. He led the Union [2][3]. Yes."
    assert claim_sentences(answer) == [
        "Lincoln was the 16th president.",
        "He led the Union.",
    ]


def test_a_bulleted_answer_splits_per_bullet():
    answer = "Facts:\n- Biotite is a mica mineral [1]\n- It is a phyllosilicate [2]"
    assert claim_sentences(answer) == ["Biotite is a mica mineral", "It is a phyllosilicate"]


def test_windows_respect_the_word_budget_and_overlap():
    sentences = [f"Sentence number {i} has exactly seven words." for i in range(10)]
    windows = premise_windows(" ".join(sentences), max_words=21)
    assert all(len(window.split()) <= 21 for window in windows)
    # Every sentence appears in at least one window, and consecutive windows share one.
    for sentence in sentences:
        assert any(sentence in window for window in windows)
    assert sentences[1] in windows[0] and sentences[1] in windows[1]


def test_a_single_long_sentence_is_still_a_window():
    long_sentence = " ".join(["word"] * 400) + "."
    assert premise_windows(long_sentence, max_words=180) == [long_sentence]


# --- the judge ----------------------------------------------------------------

class _FakeModel:
    """Entails exactly when the hypothesis text occurs in the premise."""

    def __init__(self, id2label=None):
        labels = id2label or {0: "contradiction", 1: "entailment", 2: "neutral"}
        self.config = SimpleNamespace(id2label=labels)

    def predict(self, pairs, **kwargs):
        labels = self.config.id2label.items()
        entail = next((k for k, v in labels if v.lower().startswith("entail")), 0)
        rows = []
        for premise, hypothesis in pairs:
            row = [0.0] * len(self.config.id2label)
            row[entail] = 0.9 if hypothesis.rstrip(".") in premise else 0.1
            rows.append(row)
        return rows


def test_the_entailment_label_is_read_from_the_model_not_assumed():
    model = _FakeModel({0: "ENTAILMENT", 1: "neutral", 2: "contradiction"})
    judge = NLIJudge(model=model)
    assert judge.entailment([("a cat sat", "a cat sat")]) == [0.9]


def test_a_model_without_an_entailment_label_is_rejected():
    with pytest.raises(ValueError):
        NLIJudge(model=_FakeModel({0: "positive", 1: "negative"}))


def test_support_is_the_best_window_per_sentence():
    judge = NLIJudge(model=_FakeModel())
    contexts = ["Lincoln was the 16th president. He was born in Kentucky.", "Unrelated text here."]
    scores = sentence_support(
        ["Lincoln was the 16th president.", "He was born in Paris."], contexts, judge
    )
    assert scores == [0.9, 0.1]


def test_no_context_supports_nothing():
    judge = NLIJudge(model=_FakeModel())
    assert sentence_support(["A claim with words."], [], judge) == [0.0]


class _BrittleModel(_FakeModel):
    """Entails only from SHORT premises without parentheses - the real judge's
    failure modes, measured on nli-deberta-v3-base, in miniature."""

    def predict(self, pairs, **kwargs):
        rows = []
        for premise, hypothesis in pairs:
            clean = "(" not in premise and len(premise.split()) <= 12
            entailed = clean and hypothesis.rstrip(".") in premise
            rows.append([0.0, 0.95 if entailed else 0.02, 0.0])
        return rows


def test_a_claim_lost_in_a_long_window_is_found_in_its_sentence():
    judge = NLIJudge(model=_BrittleModel())
    context = (
        "Lincoln was a lawyer in Illinois. He later moved to Washington with his family "
        "and took up a new post after a long campaign across many states."
    )
    assert sentence_support(["Lincoln was a lawyer in Illinois."], [context], judge) == [0.95]


def test_a_parenthetical_does_not_hide_the_claim_around_it():
    judge = NLIJudge(model=_BrittleModel())
    context = "Lincoln (1809-1865) was the 16th president."
    assert sentence_support(["Lincoln was the 16th president."], [context], judge) == [0.95]


def test_sentences_sharing_no_word_with_the_claim_are_not_judged():
    seen: list[str] = []

    class _Recording(_FakeModel):
        def predict(self, pairs, **kwargs):
            seen.extend(premise for premise, _ in pairs)
            return super().predict(pairs)

    context = "Lincoln was the 16th president. Pizza is a food from Naples."
    sentence_support(["Lincoln was the 16th president."], [context], NLIJudge(model=_Recording()))
    assert "Pizza is a food from Naples." not in seen
    assert "Lincoln was the 16th president." in seen


def test_taking_the_best_premise_does_not_invent_support():
    judge = NLIJudge(model=_BrittleModel())
    context = "Lincoln (1809-1865) was the 16th president. He was a lawyer."
    assert sentence_support(["Lincoln was born in Paris."], [context], judge) == [0.02]


# --- aggregation --------------------------------------------------------------

def _record(answer, answered=True, support=None, context=("Lincoln was president of the US.",)):
    return {"answered": answered, "answer": answer, "context": list(context), "support": support}


def test_refusals_are_counted_and_never_scored():
    records = [
        _record("Lincoln was president [1].", support=[0.9]),
        _record(REFUSAL, answered=False),
        _record(REFUSAL, answered=False),
    ]
    summary = summarize_answers(records)
    assert summary["n_answered"] == 1
    assert summary["n_refused"] == 2
    # Only the one real answer is judged, so the refusals cannot pull this down.
    # Lexical overlap is 3/4: "lincoln", "was", "president" - and "[1]." is a
    # token too, which is part of why this metric is kept only for continuity.
    assert summary["supported_sentence_rate"] == 1.0
    assert summary["lexical_overlap"] == pytest.approx(0.75)


def test_citation_rates_are_over_answers():
    records = [
        _record("Claim one [1]."),
        _record("Claim two [n]."),
        _record(REFUSAL, answered=False),
    ]
    summary = summarize_answers(records)
    assert summary["citation_valid_rate"] == 0.5
    assert summary["placeholder_citation_rate"] == 0.5


def test_partial_support_is_a_fraction_and_not_fully_supported():
    summary = summarize_answers([_record("A. B.", support=[0.9, 0.2])])
    assert summary["supported_sentence_rate"] == 0.5
    assert summary["fully_supported_answer_rate"] == 0.0


def test_support_keys_are_absent_when_the_judge_did_not_run():
    # "Not measured" must not read as "unsupported".
    summary = summarize_answers([_record("Claim [1].")])
    assert "supported_sentence_rate" not in summary


def test_nothing_answered_reports_none_not_zero():
    summary = summarize_answers([_record(REFUSAL, answered=False)])
    assert summary["citation_valid_rate"] is None
    assert summary["lexical_overlap"] is None


@pytest.mark.parametrize(
    "answer",
    [
        "Dave Grohl revealed in 2021 that he and Donald J. Bonebrake are cousins [1].",
        "He was the organist at the St. Jacob's Church in Citoliby [1].",
        "The treaty was signed by Dr. Smith and Gen. Jones in 1876 [2].",
    ],
)
def test_an_initial_or_abbreviation_does_not_end_a_claim(answer):
    # The first end-to-end run split all of these mid-claim, and the judge
    # scored the fragments as unsupported.
    assert len(claim_sentences(answer)) == 1


def test_real_sentence_ends_still_split():
    answer = "Lincoln was president in 1861. He led the Union. It was a war [1]."
    assert len(claim_sentences(answer)) == 3
