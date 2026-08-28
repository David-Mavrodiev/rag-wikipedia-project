"""Verify that no held-out article reached the index.

The held-out slice is what makes out-of-corpus eval cases valid at any corpus
size, but the guarantee is only worth as much as its enforcement. A single
held-out article that slips into the collection silently invalidates every
question built from it - and the eval would report a confident, wrong number
rather than an error.

This checks the invariant directly against the collection. `--repair` removes
offenders, which is needed for collections ingested before the rule existed.

Usage:
    python scripts/verify_holdout.py --collection wikipedia
    python scripts/verify_holdout.py --collection wikipedia --repair
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.holdout import HOLDOUT_MODULUS, is_held_out  # noqa: E402


def scan(client, collection: str) -> tuple[set[str], set[str]]:
    """Return (all article ids, held-out article ids present)."""
    seen: set[str] = set()
    offending: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=1024,
            offset=offset,
            with_payload=["source_id"],
            with_vectors=False,
        )
        for point in points:
            source_id = (point.payload or {}).get("source_id")
            if not source_id:
                continue
            seen.add(source_id)
            if is_held_out(source_id):
                offending.add(source_id)
        if offset is None:
            return seen, offending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="delete the offending points (needed for collections ingested "
        "before the holdout rule existed)",
    )
    args = parser.parse_args()

    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    client = QdrantClient(url=args.url, timeout=120)
    articles, offending = scan(client, args.collection)

    print(
        f"{args.collection}: {len(articles)} articles, "
        f"{len(offending)} held out (1 in {HOLDOUT_MODULUS}) and wrongly present"
    )

    if not offending:
        print("holdout-verify OK - the out-of-corpus guarantee holds")
        return 0

    if not args.repair:
        print("\n  offending article ids:", ", ".join(sorted(offending)[:10]))
        print("\nholdout-verify FAILED - questions built from these articles would")
        print("  be answerable, so every out-of-corpus case is suspect.")
        print("  Re-run with --repair to remove them.")
        return 1

    # Delete by payload filter rather than by point id: the chunk ids are
    # derived from (source_id, chunk_index) and we do not know how many chunks
    # each article produced, so matching on source_id removes all of them.
    client.delete(
        collection_name=args.collection,
        points_selector=Filter(
            must=[FieldCondition(key="source_id", match=MatchAny(any=sorted(offending)))]
        ),
        wait=True,
    )
    _, still = scan(client, args.collection)
    if still:
        print(f"repair INCOMPLETE - {len(still)} still present")
        return 1
    print(f"repaired: removed {len(offending)} held-out articles")
    print("holdout-verify OK - the out-of-corpus guarantee holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
