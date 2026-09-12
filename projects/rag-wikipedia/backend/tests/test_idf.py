"""Tests for IDF-weighted evidence scoring.

These cover the CREDIBILITY-CRITICAL half of the refusal redesign: the weighting
itself, the disabled/fallback states, and the specific failure the redesign
exists to remove — a common term shared with an irrelevant chunk buying an
accept. A bug here does not crash anything; it silently restores the weak gate
while the report still prints plausible numbers, which is exactly how the
count-based version survived as long as it did.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest
from app.core import idf, runtime_config
from app.core.config import settings
from app.core.idf import IdfTable, load_table, reset_cache, table_path
from app.core.refusal import decide_evidence, query_terms, tokenize_for_evidence


@pytest.fixture(autouse=True)
def _clear_idf_cache():
    # load_table memoizes per collection, including a None result. Without this
    # a test that runs while no table exists poisons every later test.
    reset_cache()
    yield
    reset_cache()


def _table(n_documents: int = 1000, **df: int) -> IdfTable:
    return IdfTable(collection="test", n_documents=n_documents, min_df=2, df=df)


class TestWeighting:
    def test_a_term_in_every_document_carries_no_weight(self):
        table = _table(n_documents=1000, everywhere=1000)
        assert table.weight("everywhere") == pytest.approx(0.0, abs=1e-9)

    def test_an_unknown_term_is_maximally_specific(self):
        # Not a fallback value. A term only reaches weight() by overlapping a
        # retrieved chunk, so absence from the table means rarer than min_df.
        assert _table().weight("brzezinski") == pytest.approx(1.0)

    def test_weight_falls_as_document_frequency_rises(self):
        table = _table(n_documents=1000, rare=2, middling=100, common=900)
        assert table.weight("rare") > table.weight("middling") > table.weight("common")

    def test_weight_stays_within_the_unit_interval(self):
        table = _table(n_documents=1000, rare=1, common=1000)
        for term in ("rare", "common", "absent"):
            assert 0.0 <= table.weight(term) <= 1.0

    def test_an_absolutely_rare_term_keeps_its_weight_as_the_corpus_grows(self):
        # Coverage rides mostly on the question's rare terms, so THIS is what
        # makes a threshold transfer between corpora at all. Measured across a
        # 36x change in size: 0.859 at 2,422 chunks, 0.903 at 87,173.
        small = _table(n_documents=2_422, rare=2)
        large = _table(n_documents=87_173, rare=2)
        assert small.weight("rare") == pytest.approx(large.weight("rare"), abs=0.05)
        assert min(small.weight("rare"), large.weight("rare")) > 0.8

    def test_a_fixed_proportion_term_loses_weight_as_the_corpus_grows(self):
        # The gate TIGHTENS with scale, and that is the point. The count gate it
        # replaces did the opposite — every extra article was another chance for
        # an unrelated chunk to supply the one token it needed, so
        # false_accept_rate climbed 0.450 -> 0.500 -> 0.600 with corpus size.
        # Here a word at 1% of the corpus buys 0.587 at 2,422 chunks and only
        # 0.405 at 87,173.
        #
        # An earlier version of this test asserted these were EQUAL, on an
        # assumption of scale invariance the formula does not have. The
        # assumption was wrong and the behaviour is right; the docstring that
        # claimed invariance was corrected rather than the test relaxed.
        small = _table(n_documents=2_422, one_percent=24)
        large = _table(n_documents=87_173, one_percent=872)
        assert large.weight("one_percent") < small.weight("one_percent") - 0.1

    def test_the_consequence_a_threshold_is_not_portable_between_corpora(self):
        # Direct corollary of the two tests above, pinned because it is the
        # operational rule: a coverage threshold fitted on the fixture is
        # STRICTER on the serving corpus, so it must be re-measured rather than
        # copied. This is why refusal_min_evidence_coverage is fitted per corpus:
        # the shipped 0.45 was measured on the serving corpus, and the same value
        # read off the fixture would have been the wrong number.
        question = {"one_percent", "rare"}
        evidence = {"one_percent"}
        small = _table(n_documents=2_422, one_percent=24, rare=2)
        large = _table(n_documents=87_173, one_percent=872, rare=2)
        assert large.coverage(question, evidence) < small.coverage(question, evidence)


class TestCoverage:
    def test_covering_only_common_terms_scores_near_zero(self):
        # The exact false accept the count gate produced: an unrelated chunk
        # supplies "history", the count reaches 1, the question is accepted.
        table = _table(n_documents=1000, history=800, brzezinski=2)
        assert table.coverage({"history", "brzezinski"}, {"history"}) < 0.05

    def test_covering_the_rare_term_scores_high(self):
        table = _table(n_documents=1000, history=800, brzezinski=2)
        assert table.coverage({"history", "brzezinski"}, {"brzezinski"}) > 0.95

    def test_full_coverage_is_one_and_no_coverage_is_zero(self):
        table = _table(n_documents=1000, alpha=5, beta=5)
        assert table.coverage({"alpha", "beta"}, {"alpha", "beta"}) == pytest.approx(1.0)
        assert table.coverage({"alpha", "beta"}, set()) == pytest.approx(0.0)

    def test_no_query_terms_scores_zero_rather_than_dividing_by_zero(self):
        assert _table().coverage(set(), {"anything"}) == 0.0

    def test_evidence_terms_outside_the_question_do_not_inflate_coverage(self):
        # Coverage is over the QUESTION's weight. A chunk full of rare words the
        # question never asked about is not evidence for the question.
        table = _table(n_documents=1000, asked=2)
        assert table.coverage({"asked"}, {"unrelated", "words"}) == pytest.approx(0.0)

    def test_rarest_terms_orders_by_weight_not_alphabetically(self):
        table = _table(n_documents=1000, common=900, rare=2, middling=100)
        assert table.rarest_terms({"common", "rare", "middling"}) == ["rare", "middling", "common"]


class TestTableLoading:
    def test_a_missing_table_is_none_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr(idf, "_TABLE_DIR", tmp_path)
        assert load_table("never_ingested") is None

    def test_a_corrupt_table_degrades_to_none_instead_of_crashing_retrieval(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(idf, "_TABLE_DIR", tmp_path)
        (tmp_path / "broken.json.gz").write_bytes(b"not gzip at all")
        assert load_table("broken") is None

    def test_a_table_claiming_zero_documents_is_rejected(self, tmp_path, monkeypatch):
        # n_documents reaches a log() and a division. Accepting 0 would turn a
        # malformed artifact into a ZeroDivisionError inside every query.
        monkeypatch.setattr(idf, "_TABLE_DIR", tmp_path)
        with gzip.open(tmp_path / "empty.json.gz", "wt", encoding="utf-8") as handle:
            json.dump({"collection": "empty", "n_documents": 0, "df": {}}, handle)
        assert load_table("empty") is None

    def test_a_well_formed_table_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(idf, "_TABLE_DIR", tmp_path)
        with gzip.open(tmp_path / "good.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(
                {"collection": "good", "n_documents": 500, "min_df": 2, "df": {"the": 500}},
                handle,
            )
        table = load_table("good")
        assert table is not None
        assert table.n_documents == 500
        assert table.weight("the") == pytest.approx(0.0, abs=1e-9)


class TestGateIntegration:
    """decide_evidence's behaviour with, without, and around a table."""

    def _results(self, text: str, score: float = 0.9) -> list[dict]:
        return [
            {"score": score, "text": text, "title": "T", "source_id": "1"},
            {"score": 0.5, "text": "unrelated filler", "title": "U", "source_id": "2"},
        ]

    @pytest.fixture
    def _lexical_only(self, monkeypatch):
        # Pin the vector escape hatch shut. It accepts on score alone and would
        # mask whichever lexical branch a test is actually asserting on.
        strict = replace(runtime_config.get_runtime_config(), refusal_high_confidence_score=0.99)
        monkeypatch.setattr(runtime_config, "_active_config", strict)
        return strict

    def test_without_a_table_the_count_gate_still_runs(self, tmp_path, monkeypatch, _lexical_only):
        monkeypatch.setattr(idf, "_TABLE_DIR", tmp_path / "no-tables-here")
        decision = decide_evidence("What is photosynthesis?", self._results("France is in Europe."))
        assert decision.refused is True
        assert decision.reason == "insufficient_evidence_overlap"

    def test_coverage_zero_means_disabled_not_accept_everything(self, monkeypatch, _lexical_only):
        # THE regression this guards. Read literally, `coverage >= 0.0` is always
        # true, so a default of 0.0 would accept every result clearing the score
        # floor — weaker than the gate being replaced, shipped as an upgrade.
        monkeypatch.setitem(idf._cache, settings.collection, _table(n_documents=1000, france=2))
        disabled = replace(_lexical_only, refusal_min_evidence_coverage=0.0)
        monkeypatch.setattr(runtime_config, "_active_config", disabled)

        decision = decide_evidence("What is photosynthesis?", self._results("France is in Europe."))
        assert decision.refused is True
        assert decision.reason == "insufficient_evidence_overlap"

    def test_a_shared_common_term_no_longer_buys_an_accept(self, monkeypatch, _lexical_only):
        # The count gate accepted this at refusal_min_overlap_terms=1: "history"
        # overlaps, so len(overlap) >= 1. Coverage sees a common word carrying
        # almost none of the question's weight, and refuses.
        monkeypatch.setitem(
            idf._cache, settings.collection, _table(n_documents=1000, history=900, brzezinski=2)
        )
        strict = replace(_lexical_only, refusal_min_evidence_coverage=0.35)
        monkeypatch.setattr(runtime_config, "_active_config", strict)

        decision = decide_evidence(
            "Who was Brzezinski in history?", self._results("A history of ceramics.")
        )
        assert decision.refused is True
        assert decision.reason == "insufficient_evidence_coverage"
        assert decision.coverage < 0.35
        assert "brzezinski" in decision.uncovered_terms

    def test_covering_the_rare_term_accepts(self, monkeypatch, _lexical_only):
        monkeypatch.setitem(
            idf._cache, settings.collection, _table(n_documents=1000, history=900, brzezinski=2)
        )
        strict = replace(_lexical_only, refusal_min_evidence_coverage=0.35)
        monkeypatch.setattr(runtime_config, "_active_config", strict)

        decision = decide_evidence(
            "Who was Brzezinski in history?",
            self._results("Brzezinski was a national security adviser."),
        )
        assert decision.refused is False
        assert decision.reason == "sufficient_evidence_coverage"
        assert decision.coverage > 0.35

    def test_the_vector_escape_hatch_still_rescues_a_paraphrase(self, monkeypatch):
        # A confident embedding match with no shared terms must still pass, or
        # the redesign trades false accepts for false refusals on exactly the
        # paraphrases dense retrieval exists to handle.
        monkeypatch.setitem(idf._cache, settings.collection, _table(n_documents=1000))
        strict = replace(
            runtime_config.get_runtime_config(),
            refusal_min_evidence_coverage=0.35,
            refusal_high_confidence_score=0.78,
            refusal_min_margin=0.02,
        )
        monkeypatch.setattr(runtime_config, "_active_config", strict)

        decision = decide_evidence(
            "What is the study of humankind?",
            [
                {
                    "score": 0.88,
                    "text": "Anthropology examines societies.",
                    "title": "A",
                    "source_id": "1",
                },
                {"score": 0.40, "text": "filler", "title": "B", "source_id": "2"},
            ],
        )
        assert decision.refused is False
        assert decision.reason == "high_confidence_vector_match"

    def test_the_score_floor_is_checked_before_coverage(self, monkeypatch):
        monkeypatch.setitem(idf._cache, settings.collection, _table(n_documents=1000))
        decision = decide_evidence(
            "What is photosynthesis?", self._results("Photosynthesis.", score=0.01)
        )
        assert decision.refused is True
        assert decision.reason == "score_below_minimum"


class TestBuilderContract:
    def test_the_builder_and_the_gate_share_one_tokenizer(self):
        # scripts/build_idf.py keys the table with tokenize_for_evidence. If the
        # gate ever tokenized differently, every lookup would miss, every term
        # would score 1.0, and the IDF gate would silently become an
        # accept-everything gate that still reports plausible numbers.
        text = "Brzezinski 19th-century Anthropology, revisited."
        assert tokenize_for_evidence(text) == {
            "brzezinski",
            "19th",
            "century",
            "anthropology",
            "revisited",
        }

    def test_query_terms_is_a_subset_of_the_raw_tokenization(self):
        # query_terms drops stopwords and short tokens; the evidence side does
        # not. The intersection is only meaningful if the query side is a subset
        # of the same vocabulary shape.
        question = "What is the history of anthropology?"
        assert query_terms(question) <= tokenize_for_evidence(question)


def test_the_committed_fixture_table_matches_the_fixture_collection():
    # Guards the ARTIFACT, not the code: a table rebuilt against the wrong
    # collection would score every query with the wrong corpus's frequencies,
    # and nothing at runtime would notice.
    path = table_path("wikipedia_eval")
    if not path.exists():
        pytest.skip("fixture IDF table not built (make idf-fixture)")
    table = load_table("wikipedia_eval")
    assert table is not None
    assert table.collection == "wikipedia_eval"
    assert table.n_documents > 1000
    assert Path(path).stat().st_size > 0
    # "the" is a stopword on the query side but still appears in chunk text, so
    # it must be present and near-worthless — the sanity check that the table
    # was built from real prose rather than an empty or mis-keyed scroll.
    assert table.weight("the") < 0.15
