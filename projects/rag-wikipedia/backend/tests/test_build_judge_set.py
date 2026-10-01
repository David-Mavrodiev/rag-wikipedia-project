"""The transforms that give the judge's test set its labels.

A label here is only as good as the transform: a "swapped" number that still
appears in the context, or a negation that does not negate, would be a claim
labelled unsupported that the context in fact supports - and the judge would be
marked wrong for being right. These cover the cases the first smoke run got
wrong (ordinals swapped to "33th", names taken from the very context they were
meant to be absent from, table rows passed off as sentences).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_judge_set import (  # noqa: E402
    compose,
    names_in,
    negate,
    split_of,
    swap_entity,
    swap_number,
    usable,
)

RNG = lambda: random.Random(7)  # noqa: E731


# --- what counts as a sentence ------------------------------------------------

@pytest.mark.parametrize("text", [
    "The club was founded in 1905 following the amalgamation of two village teams.",
    "Drainage of the marshes destroyed almost the whole wetlands complex of the valley.",
])
def test_prose_is_usable(text):
    assert usable(text)


@pytest.mark.parametrize("text", [
    "Organization Combat units 1st Infantry Regiment Colorados BI-201 BATCOM-251 Gen.",
    "Short one.",
    "He was the only founding member from California who stayed with the band for years.",
    "A sentence that never ends with a full stop and so was never a sentence at all",
])
def test_lists_fragments_and_pronoun_openers_are_not(text):
    assert not usable(text)


# --- the swaps ----------------------------------------------------------------

def test_a_swapped_number_is_absent_from_the_context():
    sentence = "The stand was built in 1958 and held 2000 spectators."
    context = sentence + " Another stand followed in 1961."
    swapped = swap_number(sentence, context, RNG())
    assert swapped and swapped != sentence
    changed = [word for word in swapped.split() if word not in context.split()]
    assert changed, "something must have changed"
    for word in changed:
        assert word.strip(".") not in context


def test_an_ordinal_is_not_a_number_to_swap():
    # "In the late 20th century" became "the late 33th century" in the first run.
    assert swap_number("In the late 20th century the valley changed character.", "", RNG()) is None


def test_a_sentence_without_a_number_cannot_be_number_swapped():
    assert swap_number("The valley changed character over many years.", "", RNG()) is None


def test_a_swapped_name_never_comes_from_the_context_it_must_be_absent_from():
    sentence = "The altar was made by Francesco Robba for the parish church."
    context = sentence + " Mozirje lies on the Savinja River."
    assert swap_entity(sentence, context, ["Mozirje"], RNG()) is None
    swapped = swap_entity(sentence, context, ["Karel Kopriva"], RNG())
    assert swapped == "The altar was made by Karel Kopriva for the parish church."


def test_the_name_pool_skips_sentence_openers():
    text = "Thus the valley changed. Historically the altar by Francesco Robba stood here."
    pool = names_in(text)
    assert "Francesco Robba" in pool
    assert "Thus" not in pool and "Historically" not in pool


# --- negation and composition -------------------------------------------------

def test_negation_inserts_not_after_the_auxiliary():
    assert negate("The mall is managed by Simon Property Group.") == \
        "The mall is not managed by Simon Property Group."


def test_an_already_negative_sentence_is_left_alone():
    assert negate("The name has not survived in any record of the abbey.") is None


def test_a_sentence_without_an_auxiliary_cannot_be_negated():
    assert negate("Kiselyov marched with the army all the way to Paris.") is None


def test_composing_keeps_both_halves_in_one_claim():
    joined = compose("Lincoln was the 16th president.", "He was born in Kentucky.")
    assert joined == "Lincoln was the 16th president; He was born in Kentucky."


# --- the split ----------------------------------------------------------------

def test_a_title_always_lands_in_the_same_split():
    assert split_of("Billerbeck") == split_of("Billerbeck")


def test_both_splits_are_used():
    splits = {split_of(f"Article {index}") for index in range(40)}
    assert splits == {"dev", "test"}
