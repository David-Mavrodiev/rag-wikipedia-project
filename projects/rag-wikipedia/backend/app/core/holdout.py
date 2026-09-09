"""The held-out slice: articles deliberately never ingested.

"Out of corpus" is a RELATION between a question and a corpus, not a property of
a question. Hand-listing out-of-corpus questions bakes in an assumption about
what the corpus contains, and that assumption expires the moment the corpus
grows: "What is the speed of light?" is unanswerable from 150 articles, answerable
from 25,000, and trivially answerable from all of Wikipedia.

So the out-of-corpus set is derived instead. A deterministic fraction of articles
is excluded from ingestion, and questions are built from those. They are
unanswerable **by construction**, at any corpus size, with no list to maintain.

The rule is a hash of the article id, which gives three properties that matter:

* deterministic - the same articles are held out on every re-ingest, so a
  resumed or repeated run cannot accidentally ingest one;
* independent of stream order and of corpus size - holding out 1% of 25,000
  articles and 1% of 6.8M uses the identical rule, no edit required;
* independent of content - nothing about the article text or title influences
  the decision, so the held-out set is not biased toward any topic.

The guarantee is only as good as its enforcement, so `verify_no_holdout_ingested`
turns it into something checkable rather than something believed.
"""

from __future__ import annotations

import hashlib

# 1 in 100 articles. At 25k that is ~250 held-out articles - enough to build a
# meaningful suite from - while costing 1% of corpus coverage.
HOLDOUT_MODULUS = 100


def is_held_out(article_id: str | int) -> bool:
    """True when this article must never be ingested.

    Uses SHA-256 rather than Python's hash(): the built-in is salted per process
    (PYTHONHASHSEED), so it would select a *different* 1% on every run and
    destroy the guarantee entirely.
    """
    digest = hashlib.sha256(str(article_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % HOLDOUT_MODULUS == 0


def held_out_fraction() -> float:
    return 1.0 / HOLDOUT_MODULUS
