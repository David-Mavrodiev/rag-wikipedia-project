"""Build the document-frequency table that `app.core.idf` scores evidence with.

WHY THIS EXISTS
---------------
`decide_evidence` used to accept on a COUNT of overlapping terms
(`refusal_min_overlap_terms = 1`): one shared token between the question and any
retrieved chunk was enough. That gate gets WEAKER as the corpus grows, because
every additional article is another chance for an irrelevant chunk to supply
that one token. Measured across three corpus sizes, false_accept_rate rose
monotonically: 0.450 at 60 articles, 0.500 at 500, 0.600 at 24,694.

Weighting the overlap by term specificity removes that mechanism. A question's
rare terms are the ones that identify its topic; a chunk that shares only common
words supplies almost no evidence weight no matter how large the corpus gets.

DOCUMENT FREQUENCY IS COUNTED PER CHUNK, NOT PER ARTICLE
--------------------------------------------------------
Deliberate, and it is the part most likely to be questioned in review. Two
reasons:

  1. The chunk is the retrieval unit. Evidence overlap is tested against
     retrieved CHUNKS, so "how many chunks contain this term" is the frequency
     that matches what the gate actually scores.
  2. It streams in bounded memory. Article-level DF needs the set of terms per
     article held until every one of that article's chunks has been seen, and
     Qdrant's scroll returns points in id order, not grouped by article. At
     24,694 articles that is millions of live set entries; chunk-level DF is a
     single Counter over the vocabulary.

The table is built from what is INDEXED, not from a corpus file on disk, so it
describes the collection that is actually served.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.idf import DEFAULT_MIN_DF, table_path  # noqa: E402
from app.core.refusal import tokenize_for_evidence  # noqa: E402


def scroll_chunks(qdrant_url: str, collection: str, batch: int):
    """Yield every chunk's text, one page at a time."""
    offset = None
    seen = 0
    with httpx.Client(timeout=120.0) as client:
        while True:
            body = {"limit": batch, "with_payload": ["text"], "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = client.post(
                f"{qdrant_url}/collections/{collection}/points/scroll", json=body
            )
            response.raise_for_status()
            result = response.json()["result"]
            points = result.get("points", [])
            if not points:
                return
            for point in points:
                text = (point.get("payload") or {}).get("text") or ""
                if text:
                    yield text
            seen += len(points)
            print(f"  scrolled {seen} chunks", file=sys.stderr, end="\r")
            offset = result.get("next_page_offset")
            if offset is None:
                return


def build(qdrant_url: str, collection: str, batch: int, min_df: int) -> dict:
    document_frequency: Counter[str] = Counter()
    n_chunks = 0

    for text in scroll_chunks(qdrant_url, collection, batch):
        n_chunks += 1
        # set(), not list: a term occurring five times in one chunk is still
        # present in exactly one document. Counting occurrences here would make
        # DF a term-frequency table and invert the weighting for repeated words.
        document_frequency.update(tokenize_for_evidence(text))

    print(file=sys.stderr)
    if n_chunks == 0:
        raise SystemExit(f"collection {collection!r} yielded no chunks with text")

    # Terms below `min_df` are DROPPED, and `app.core.idf` scores any term
    # missing from the table as maximally specific. That is not an
    # approximation for convenience — it is the correct reading. A term can only
    # contribute to overlap if it appears in a retrieved chunk, so a term absent
    # from the table is either rare enough to be highly discriminative or absent
    # from the corpus entirely, and both deserve full weight. Dropping the long
    # tail is what keeps the artifact small enough to commit.
    kept = {term: count for term, count in document_frequency.items() if count >= min_df}

    return {
        "version": 1,
        "collection": collection,
        "n_documents": n_chunks,
        "unit": "chunk",
        "min_df": min_df,
        "vocabulary_total": len(document_frequency),
        "vocabulary_kept": len(kept),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "df": dict(sorted(kept.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--min-df", type=int, default=DEFAULT_MIN_DF)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    table = build(args.qdrant_url, args.collection, args.batch, args.min_df)
    out = args.out or table_path(args.collection)
    out.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(table, ensure_ascii=False, separators=(",", ":"))
    # mtime=0 so rebuilding an unchanged corpus produces a byte-identical file
    # and does not show up as a spurious diff.
    with gzip.GzipFile(out, "wb", mtime=0) as handle:
        handle.write(payload.encode("utf-8"))

    print(
        f"{out}: {table['n_documents']} chunks, "
        f"{table['vocabulary_kept']}/{table['vocabulary_total']} terms kept "
        f"(df >= {args.min_df}), {out.stat().st_size / 1024:.0f} KiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
