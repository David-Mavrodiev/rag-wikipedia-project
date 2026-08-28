"""Build an evaluation suite for the SERVING corpus, derived from it.

The committed fixture suite describes 150 articles and stays the reproducible CI
baseline. It cannot describe the 25k serving corpus: its out-of-corpus cases
("What is the speed of light?", "Who invented the telephone?") are answerable
from 25,000 articles, so scoring them there produces a confident wrong number.

This generates a suite for whatever corpus is actually loaded, using the held-out
slice as the source of unanswerable questions. Those articles were excluded from
ingestion by `app.core.holdout`, so questions about them are unanswerable BY
CONSTRUCTION at any corpus size - there is no list to maintain and nothing
expires when the corpus grows.

THE VERIFICATION RULE, which is the part that matters:

    Verify CORPUS properties. Never verify SYSTEM BEHAVIOUR.

A generator that kept only the cases the retriever already refuses would produce
a suite scoring 1.0 by construction and measuring nothing. So an answerable case
is checked only for "this article is in the index", and an unanswerable case only
for "no article covering this topic is in the index". Whether retrieval then gets
it right is what the eval is for, and this script must not pre-decide it.

WHAT THIS SUITE MEASURED (2026-08-27, `wikipedia` = 24,694 articles / 87,173
vectors, k=5, bge-small-en-v1.5, 90 cases):

    recall@5           0.933
    mrr                0.904
    precision@5        0.383
    refusal_accuracy   0.400
    false_accept_rate  0.600
    answerable_refusal 0.000

Read against the 150-article fixture suite (recall 1.000, mrr 1.000, precision
0.930, refusal 0.500, false accepts 0.500), two things separate cleanly:

RETRIEVAL SCALES. At 165x the corpus, recall@5 fell only 1.000 -> 0.933 and MRR
1.000 -> 0.904. The right article is still usually found and usually ranked
first. precision@5 dropping to 0.383 is mostly the metric's shape rather than a
regression: with one expected title and k=5, four of five retrieved chunks are
"wrong" by construction, so the ceiling is low and falls as the corpus offers
more plausible neighbours.

REFUSAL DOES NOT. false_accept_rate is now measured at three corpus sizes and
rises monotonically:

    60 articles      0.450
    500 articles     0.500
    24,694 articles  0.600

The cause is structural, not a tuning miss. `refusal_min_overlap_terms = 1`
accepts on a single shared token between the question and any retrieved chunk.
Every additional article is another chance for an irrelevant chunk to supply
that token, so the gate gets weaker precisely as the corpus gets larger - the
opposite of what a quality control should do. A 4x3 sweep of
refusal_min_score x refusal_min_overlap_terms found no configuration clearing
the gates without also refusing "Who was Abraham Lincoln?", so this needs a
design change (evidence scoring that considers WHICH terms overlap and how
specific they are), not a threshold.

This is the number the refusal redesign should be judged against, and it is the
reason these cases had to stop being a hardcoded list: at 24,694 articles the
old out-of-corpus questions ("speed of light", "the telephone", "the Nile") are
all answerable, and scoring them here would have reported a confident 1.000.

Usage:
    uv run python scripts/build_serving_suite.py --collection wikipedia
    uv run python scripts/build_serving_suite.py --collection wikipedia
        --answerable 60 --unanswerable 20
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.holdout import HOLDOUT_MODULUS, is_held_out  # noqa: E402

OUT = BACKEND / "eval" / "serving_golden.jsonl"

# Personal or time-bound questions no static snapshot can answer. These exercise
# the intent filter; the held-out cases exercise the evidence gate. A suite needs
# both, or refusal accuracy grades one mechanism twice.
TRIGGER_CASES = [
    ("What is the password for my laptop?", "private"),
    ("What did my manager say about me in private?", "private"),
    ("What is my current account balance?", "time_dependent"),
    ("What did I have for breakfast this morning?", "private"),
    ("What is on my calendar tomorrow?", "time_dependent"),
    ("Where did I leave my keys yesterday?", "private"),
    ("What is my private recovery phrase?", "private"),
    ("Who is the current president?", "time_dependent"),
    ("What is the weather right now?", "time_dependent"),
    ("What is the security code for my apartment building?", "private"),
]


def _question(title: str, variant: int) -> str:
    # Two neutral templates that read naturally for both people and things.
    return f"What is {title}?" if variant % 2 == 0 else f"Tell me about {title}."


def indexed_titles(client, collection: str) -> set[str]:
    titles: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=4096,
            offset=offset,
            with_payload=["title"],
            with_vectors=False,
        )
        for point in points:
            title = (point.payload or {}).get("title")
            if title:
                titles.add(title)
        if offset is None:
            return titles


def collect_held_out(profile: str, wanted: int, scan_limit: int) -> list[dict]:
    """Stream until `wanted` held-out articles are found."""
    from pipeline.sources import iter_articles

    found: list[dict] = []
    for index, article in enumerate(iter_articles(profile)):
        if index >= scan_limit:
            break
        if is_held_out(article["id"]):
            found.append(article)
            if len(found) >= wanted:
                break
    return found


def _tokens(title: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", title.lower()))


def _covered_elsewhere(title: str, indexed_tokens: dict[frozenset[str], str]) -> str | None:
    """Is this held-out topic covered by an article that IS indexed?

    Holding out `Apollo` does not make it unanswerable if `Apollo 11` is
    indexed. Matching is on WHOLE TOKENS, not substrings: a naive
    `title in other` rejects "Ada" because "Canada" contains "ada", and it threw
    away 51 of 60 candidates on the first run. (The same substring trap
    previously inflated evidence_overlap in refusal.py.)

    A lexical approximation, not proof - stated as such rather than presented as
    a guarantee.
    """
    mine = _tokens(title)
    if not mine:
        return None
    for other_tokens, other in indexed_tokens.items():
        if mine <= other_tokens or other_tokens <= mine:
            return other
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="wikipedia")
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--profile", default="real")
    parser.add_argument("--answerable", type=int, default=60)
    parser.add_argument("--unanswerable", type=int, default=20)
    parser.add_argument("--scan-limit", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=20260827)
    args = parser.parse_args()

    from qdrant_client import QdrantClient

    rng = random.Random(args.seed)
    client = QdrantClient(url=args.url, timeout=180)

    indexed = indexed_titles(client, args.collection)
    # token-set -> a representative title, so coverage checks are whole-token
    indexed_tokens = {_tokens(t): t for t in indexed if _tokens(t)}
    print(f"corpus: {len(indexed)} distinct titles in '{args.collection}'")

    # --- answerable: sampled from what IS indexed -------------------------
    # Verified only for presence in the index. Whether retrieval finds them is
    # precisely what the eval measures, so it is not checked here.
    pool = sorted(t for t in indexed if len(t) > 3 and not t.endswith("(disambiguation)"))
    rng.shuffle(pool)
    answerable = [
        {
            "id": f"sg-a-{i:03d}",
            "category": "factual",
            "question": _question(title, i),
            "expected_titles": [title],
            "expected_refusal": False,
        }
        for i, title in enumerate(pool[: args.answerable])
    ]

    # --- unanswerable: held-out articles, out of corpus by construction ----
    candidates = collect_held_out(args.profile, args.unanswerable * 3, args.scan_limit)
    print(f"held-out articles scanned: {len(candidates)} (1 in {HOLDOUT_MODULUS})")

    unanswerable = []
    rejected = 0
    for article in candidates:
        if len(unanswerable) >= args.unanswerable:
            break
        title = article["title"]
        if title in indexed:
            # Would mean the holdout was not enforced - verify_holdout.py exists
            # for exactly this, so refuse to paper over it here.
            print(f"  ERROR: held-out '{title}' IS indexed - run verify_holdout.py --repair")
            return 1
        clash = _covered_elsewhere(title, indexed_tokens)
        if clash:
            rejected += 1
            continue
        unanswerable.append(
            {
                "id": f"sg-u-{len(unanswerable):03d}",
                "category": "out_of_corpus",
                "question": _question(title, len(unanswerable)),
                "expected_titles": [],
                "expected_refusal": True,
                "held_out_article": title,
            }
        )

    trigger = [
        {
            "id": f"sg-t-{i:03d}",
            "category": category,
            "question": question,
            "expected_titles": [],
            "expected_refusal": True,
        }
        for i, (question, category) in enumerate(TRIGGER_CASES)
    ]

    rows = answerable + unanswerable + trigger
    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )

    print(
        f"wrote {OUT.relative_to(BACKEND.parent)}\n"
        f"  answerable            : {len(answerable)}\n"
        f"  out-of-corpus (held out): {len(unanswerable)}"
        f"  ({rejected} rejected as covered elsewhere)\n"
        f"  trigger-word          : {len(trigger)}\n"
        f"  total                 : {len(rows)}"
    )
    if len(unanswerable) < args.unanswerable:
        print("\nWARNING: fewer out-of-corpus cases than requested - raise --scan-limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
