"""Term-specificity weighting for the evidence gate.

The gate this replaces counted overlapping terms and accepted at
`refusal_min_overlap_terms = 1`. Counting treats every shared token as equal
evidence, so "history", "known" or "world" landing in an unrelated chunk passed
a question the corpus could not answer. Worse, the failure SCALES THE WRONG WAY:
each additional article is one more chance for some irrelevant chunk to supply
that token, so false_accept_rate climbed with corpus size (0.450 at 60 articles,
0.500 at 500, 0.600 at 24,694) — a quality control that weakens as the system
grows.

Weighting by inverse document frequency removes the mechanism rather than
tuning around it. A term's weight is how much it NARROWS the corpus, so common
words contribute almost nothing however many chunks contain them, and the
question's rare terms — the ones that actually name its topic — are the ones
that must be covered.

This module is deliberately tokenizer-agnostic: it takes term sets and returns a
number. `app.core.refusal` owns tokenization and calls in here, which keeps the
dependency one-way and lets the table builder share the tokenizer without a
cycle.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Terms rarer than this are omitted from the committed table and scored as
# maximally specific on lookup. See `weight` for why that is exact rather than
# an approximation.
DEFAULT_MIN_DF = 2

_TABLE_DIR = Path(__file__).resolve().parents[2] / "eval" / "idf"


def table_path(collection: str) -> Path:
    return _TABLE_DIR / f"{collection}.json.gz"


@dataclass(frozen=True)
class IdfTable:
    collection: str
    n_documents: int
    min_df: int
    df: dict[str, int]

    @property
    def _log_n(self) -> float:
        return math.log(self.n_documents + 1)

    def weight(self, term: str) -> float:
        """Specificity of *term* in [0, 1]: 1.0 = names one chunk, 0.0 = in all.

        Smoothed IDF normalized by its own maximum. Measured across a 36x
        change in corpus size (2,422 -> 87,173 chunks) the normalization behaves
        in two distinct ways, and BOTH matter:

          * An absolutely rare term is stable: df=2 scores 0.859 at 2,422 and
            0.903 at 87,173. Coverage rides mostly on these, which is why a
            threshold transfers between corpora at all.
          * A term at a fixed PROPORTION of the corpus loses weight as the
            corpus grows: 1% of chunks scores 0.587 at 2,422 but 0.405 at
            87,173. So the gate TIGHTENS with scale.

        The second is not scale-invariance and this docstring used to claim it
        was. It is better than invariance for this gate's purpose: the failure
        being fixed is a gate that WEAKENED as the corpus grew, and here a
        common word buys less the bigger the corpus gets. The cost is that a
        threshold is not exactly portable between corpora — it must be measured
        against the corpus it will run on, which is why `refusal_min_evidence_
        coverage` ships disabled rather than at a fixture-fitted value.

        A term MISSING from the table scores 1.0. That is the correct reading,
        not a fallback: a term can only reach this function through overlap with
        a retrieved chunk, so it is either rarer than `min_df` — genuinely
        discriminative — or absent from the corpus, in which case it is
        uncoverable and should count fully against coverage.
        """
        document_frequency = self.df.get(term, 0)
        return math.log((self.n_documents + 1) / (document_frequency + 1)) / self._log_n

    def coverage(self, query_terms: set[str], evidence_terms: set[str]) -> float:
        """Fraction of the question's SPECIFICITY that the evidence supplies.

        Not the fraction of terms — the fraction of weight. Covering "what" and
        "history" while missing "brzezinski" scores near zero, which is the
        judgement the count-based gate could not make.
        """
        if not query_terms:
            return 0.0
        total = sum(self.weight(term) for term in query_terms)
        if total <= 0.0:
            return 0.0
        covered = sum(self.weight(term) for term in query_terms & evidence_terms)
        return covered / total

    def rarest_terms(self, query_terms: set[str], limit: int = 3) -> list[str]:
        """The query terms carrying the most weight — what a refusal is ABOUT.

        Surfaced in the decision so an operator reading a refusal sees which
        terms went uncovered instead of a bare threshold comparison.
        """
        return sorted(query_terms, key=lambda term: (-self.weight(term), term))[:limit]


_cache: dict[str, IdfTable | None] = {}


def load_table(collection: str) -> IdfTable | None:
    """Return the table for *collection*, or None when it has not been built.

    None is a supported state, not an error: a fresh checkout has no table for a
    collection it has never ingested, and `decide_evidence` falls back to the
    count gate rather than refusing everything. The fallback is logged once —
    silently serving the weaker gate is how this class of regression hides.
    """
    if collection in _cache:
        return _cache[collection]

    path = table_path(collection)
    table: IdfTable | None = None
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                raw = json.load(handle)
            table = IdfTable(
                collection=raw["collection"],
                n_documents=int(raw["n_documents"]),
                min_df=int(raw.get("min_df", DEFAULT_MIN_DF)),
                df=raw["df"],
            )
            if table.n_documents <= 0:
                raise ValueError(f"n_documents={table.n_documents} must be positive")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            logger.warning(
                "IDF table %s is unreadable (%s); falling back to term counts", path, error
            )
            table = None
    else:
        logger.warning("No IDF table at %s; evidence falls back to term counts", path)

    _cache[collection] = table
    return table


def reset_cache() -> None:
    """Drop the memoized tables. For tests and for a post-rebuild reload."""
    _cache.clear()
