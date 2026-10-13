"""The committed eval suites are well-formed, and the detail suites are what they claim.

A suite that is wrong measures something other than its name. These check the
properties that can be checked without a vector store - and for the fixture's
detail suite that is nearly everything, because the fixture corpus is committed.
The serving detail suite's corpus checks (answer present in its article, absent
from all 87,173 indexed chunks) need Qdrant and run in
scripts/build_detail_suite.py when it is built.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from app.core.holdout import is_held_out
from eval.run_eval import validate_golden_set
from eval.suites import (
    SUITE_PROFILES,
    SuiteProfileMismatch,
    check_profile,
    load_suite,
    names_its_title,
    normalize,
    title_words,
)

FIXTURE = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "corpus_eval.jsonl.gz"
DETAIL_SUITES = ("detail", "serving_detail")


def _fixture() -> dict[str, dict]:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return {row["title"]: row for row in map(json.loads, handle)}


@pytest.mark.parametrize("name", sorted(SUITE_PROFILES))
def test_every_registered_suite_exists_and_validates(name):
    # A suite in the registry that cannot be loaded or would be rejected by the
    # harness is a name without a measurement behind it.
    suite = load_suite(name)
    validate_golden_set(suite)
    ids = [case["id"] for case in suite if "id" in case]
    assert len(ids) == len(set(ids)), f"{name} has duplicate ids"


def test_a_suite_is_refused_against_another_corpus():
    check_profile("detail", "fixture")
    with pytest.raises(SuiteProfileMismatch):
        check_profile("serving_detail", "fixture")


def test_a_disambiguator_is_not_part_of_the_name():
    assert title_words("Aries (constellation)") == {"aries"}
    assert names_its_title("Which planet is closest?", "Mercury (planet)") == set()


def test_an_acronym_names_its_article():
    # Found in review: "AFC Championship Game" for American Football Conference.
    assert names_its_title("Who won the 2022 AFC Championship Game?",
                           "American Football Conference") == {"afc"}
    # Two-letter acronyms are ignored: "as" is an English word, not "Aegean Sea".
    assert names_its_title("What was known as the Hellespont?", "Aegean Sea") == set()


def test_non_ascii_titles_are_whole_words():
    # "[a-z0-9]+" once split this into "c", "sar", "ram", "rez", so "K. C. Jones"
    # read as naming the article.
    assert title_words("César Ramírez (footballer)") == {"césar", "ramírez"}
    assert names_its_title("Who succeeded K. C. Jones?", "César Ramírez (footballer)") == set()


@pytest.mark.parametrize("name", DETAIL_SUITES)
def test_no_detail_question_names_its_article(name):
    # The one property that separates these suites from the title suites.
    for case in load_suite(name):
        assert not names_its_title(case["question"], case["source_article"]), case["id"]


@pytest.mark.parametrize("name", DETAIL_SUITES)
def test_no_detail_question_contains_its_answer(name):
    for case in load_suite(name):
        assert normalize(case["answer"]) not in normalize(case["question"]), case["id"]


@pytest.mark.parametrize("name", DETAIL_SUITES)
def test_every_detail_case_shows_its_evidence(name):
    # The reviewer's view: the passage sentence holding the answer.
    for case in load_suite(name):
        assert normalize(case["answer"]) in normalize(case["evidence"]), case["id"]


def test_fixture_detail_answers_are_in_their_article():
    fixture = _fixture()
    for case in load_suite("detail"):
        if case["expected_refusal"]:
            continue
        article = fixture[case["expected_titles"][0]]
        assert not is_held_out(article["id"]), f"{case['id']}: article is held out"
        assert normalize(case["answer"]) in normalize(article["text"]), case["id"]


def test_fixture_detail_unanswerable_answers_occur_nowhere_in_the_fixture():
    # Checked against all 150 fixture articles - a superset of what is indexed.
    fixture = _fixture()
    texts = [normalize(article["text"]) for article in fixture.values()]
    for case in load_suite("detail"):
        if case["expected_refusal"]:
            assert case["source_article"] not in fixture, case["id"]
            answer = normalize(case["answer"])
            assert not any(answer in text for text in texts), case["id"]
