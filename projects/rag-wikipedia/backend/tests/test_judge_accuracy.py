"""Measuring the judge: the bookkeeping that decides whether a change is real.

The judge model is faked here. What is under test is what the harness claims
about it - that a flag's precision is computed over the flags, that name-only
claims stay out of the rates, that a kind cannot hide inside an average, and
that assembling evidence is reported as a variant rather than silently applied.
"""

from __future__ import annotations

import gzip
import json

from eval.grounding import assembled_premise, content_terms, sentence_support
from eval.judge_accuracy import (
    build_markdown,
    calibration,
    confusion,
    load_claims,
    main,
    per_kind,
    score_claims,
)

CHUNK = "Lincoln was the 16th president. He was born in Kentucky. Pizza comes from Naples."


def _claim(cid, label, claim=CHUNK.split(".")[0] + ".", kind=None, split="dev"):
    row = {"id": cid, "label": label, "claim": claim, "split": split,
           "context": [{"title": "Lincoln", "text": CHUNK}]}
    if kind:
        row["kind"] = kind
    return row


class _Fake:
    """Entails when the hypothesis occurs in the premise."""

    def __init__(self):
        labels = {0: "contradiction", 1: "entailment", 2: "neutral"}
        self.config = type("c", (), {"id2label": labels})()

    def predict(self, pairs, **kwargs):
        return [[0.0, 0.9 if hypothesis.rstrip(".") in premise else 0.05, 0.0]
                for premise, hypothesis in pairs]


# --- the rates ----------------------------------------------------------------

def test_precision_is_over_the_flags_and_recall_over_the_unsupported():
    claims = [_claim("a", "unsupported"), _claim("b", "supported"),
              _claim("c", "unsupported"), _claim("d", "supported")]
    scores = [0.1, 0.2, 0.8, 0.9]  # flags a and b; misses c
    result = confusion(claims, scores, threshold=0.5)
    assert result["flagged_and_unsupported"] == ["a"]
    assert result["flagged_but_supported"] == ["b"]
    assert result["passed_but_unsupported"] == ["c"]
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["agreement"] == 0.5


def test_a_name_only_claim_is_counted_apart_not_as_a_failure():
    claims = [_claim("a", "supported"), _claim("n", "name_only")]
    result = confusion(claims, [0.9, 0.02], threshold=0.5)
    assert result["n"] == 1 and result["agreement"] == 1.0
    assert result["name_only"] == {"n": 0.02}


def test_a_contradicted_claim_counts_as_one_the_judge_should_flag():
    result = confusion([_claim("a", "contradicted")], [0.1], threshold=0.5)
    assert result["flagged_and_unsupported"] == ["a"] and result["recall"] == 1.0


def test_rates_are_none_rather_than_zero_when_nothing_qualifies():
    result = confusion([_claim("a", "supported")], [0.9], threshold=0.5)
    assert result["precision"] is None and result["recall"] is None


def test_each_kind_is_reported_separately():
    claims = [_claim("a", "unsupported", kind="negated"),
              _claim("b", "unsupported", kind="negated"),
              _claim("c", "supported", kind="verbatim")]
    result = per_kind(claims, [0.1, 0.9, 0.9], threshold=0.5)
    assert result["negated"] == {"n": 2, "agreement": 0.5}
    assert result["verbatim"] == {"n": 1, "agreement": 1.0}


def test_calibration_compares_a_score_with_how_often_it_is_right():
    claims = [_claim(str(i), "supported") for i in range(4)] + [_claim("x", "unsupported")]
    result = calibration(claims, [0.9, 0.9, 0.9, 0.9, 0.9], bins=5)
    bucket = result["bins"][-1]
    assert bucket["n"] == 5 and bucket["share_supported"] == 0.8
    assert result["ece"] == 0.1  # claims 0.9, right 0.8 of the time


# --- scoring and reporting ----------------------------------------------------

def test_every_claim_is_scored_against_its_own_context():
    claims = [_claim("a", "supported"), _claim("b", "unsupported", claim="Lincoln flew a plane.")]
    scores = score_claims(claims, _judge())
    assert scores[0] > 0.5 > scores[1]


def _judge():
    from eval.grounding import NLIJudge

    return NLIJudge(model=_Fake())


def test_the_report_names_the_variant_and_lists_what_the_rate_hides():
    report = {
        "set": "real_claims", "split": "dev", "judge": "fake", "assemble": 3,
        "confusion": confusion([_claim("a", "unsupported"), _claim("b", "supported"),
                                _claim("n", "name_only")], [0.9, 0.1, 0.4], threshold=0.5),
        "per_kind": per_kind([_claim("a", "unsupported")], [0.9], threshold=0.5),
        "calibration": {"bins": [], "ece": None},
    }
    markdown = build_markdown(report)
    assert "assemble 3" in markdown
    assert "`a`" in markdown.split("Passed but unsupported")[1]
    assert "`b`" in markdown.split("Flagged but supported")[1]
    assert "`n` 0.40" in markdown


def test_a_labelled_set_round_trips_through_gzip(tmp_path):
    path = tmp_path / "set.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(_claim("a", "supported")) + "\n")
    assert load_claims(path)[0]["id"] == "a"


def test_a_missing_set_is_an_error_not_an_empty_measurement(tmp_path):
    assert main(["--set", "path", "--path", str(tmp_path / "nope.jsonl.gz")]) == 2


# --- the variant --------------------------------------------------------------

def test_assembling_joins_the_relevant_sentences_in_document_order():
    terms = content_terms("Lincoln was born in Kentucky.")
    premise = assembled_premise(CHUNK, terms, k=3)
    assert premise.startswith("Lincoln was the 16th president.")
    assert "Kentucky" in premise and "Naples" not in premise


def test_assembling_needs_two_sentences_to_be_worth_a_premise():
    assert assembled_premise(CHUNK, content_terms("Naples pizza"), k=3) is None


class _NeedsEveryWord(_Fake):
    """Entails only when the premise carries every content word of the claim.

    The real judge's composition failure in miniature: the two halves of the
    claim sit in sentences too far apart for a window or an adjacent pair.
    """

    def predict(self, pairs, **kwargs):
        rows = []
        for premise, hypothesis in pairs:
            covered = content_terms(hypothesis) <= content_terms(premise)
            rows.append([0.0, 0.9 if covered else 0.05, 0.0])
        return rows


def test_assembling_is_what_makes_a_split_claim_reachable():
    from eval.grounding import NLIJudge

    spread = ("Lincoln was the 16th president. " + "Filler sentence here. " * 100
              + "He was born in Kentucky.")
    claim = ["Lincoln was the 16th president born in Kentucky."]
    without = sentence_support(claim, [spread], NLIJudge(model=_NeedsEveryWord()))
    with_assembly = sentence_support(claim, [spread], NLIJudge(model=_NeedsEveryWord()),
                                     assemble=3)
    assert without == [0.05], "no window or pair holds both halves"
    assert with_assembly == [0.9], "the assembled premise does"
