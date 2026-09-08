from __future__ import annotations

import logging
import sys
from pathlib import Path

from prefect import flow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def _run_logger():
    """Prefect's run logger inside a flow run, the module logger otherwise.

    Inside a flow, records emitted through the module logger go to Prefect's
    handler and may not reach stdout - which is where a detached `nohup` run is
    read from. get_run_logger() routes correctly, but raises outside a flow
    context, and the tests call ingest_flow.fn() directly.
    """
    try:
        from prefect import get_run_logger

        return get_run_logger()
    except Exception:  # no flow context (tests, direct calls)
        return logger

# Articles per segment. Chosen so the embedder gets a real batch even deep in
# the stream, where articles average ~2.4 chunks: 200 articles is ~500 chunks
# there, and ~2,000 near the start.
SEGMENT_ARTICLES = 200


@flow(name="wikipedia-ingest")
def ingest_flow(profile: str | None = None, *, force: bool = False):
    """Ingest a Wikipedia profile into Qdrant. Resumable and batched.

    Embedding is ~97% of ingestion time, and the `real` profile is hours of it.
    Deterministic point IDs already made the *upsert* idempotent, but a re-run
    still re-embedded every article, so an interruption meant beginning again.

    Two properties this flow keeps:

    * RESUMABLE - each segment asks Qdrant which of its point IDs already exist
      and embeds only what is missing, so a re-run costs one lookup per segment
      rather than the whole embedding bill.
    * BATCHED - articles are accumulated into segments so the existence check,
      the embed call and the upserts are amortised. Per-article, those fixed
      costs dominate once chunk density falls (measured: 11.1 chunks/article at
      the start of the stream, 2.4 by article 12,000), and throughput collapsed
      from 24 to 1 chunk/s.

    Pass ``force=True`` to re-embed and overwrite regardless - needed when the
    embedding model or the chunking parameters change, because the point IDs
    stay the same while the vectors they should hold do not.
    """
    from app.core.config import settings
    from app.core.embeddings import BGEEmbedder
    from app.core.holdout import HOLDOUT_MODULUS, is_held_out
    from app.core.vectorstore import QdrantStore

    from pipeline.sources import iter_articles_resilient
    from pipeline.tasks import ingest_segment, prepare_article

    profile = profile or settings.profile
    embedder = BGEEmbedder(model_name=settings.embed_model)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)
    # Asked of the embedder, never hardcoded: the collection must be created at
    # whatever length THIS model produces, or the first upsert is rejected.
    store.ensure_collection(dim=embedder.dim)

    log = _run_logger()
    inserted_total = 0
    skipped_chunks = 0
    seen_articles = 0
    held_out = 0
    segment: list[tuple[dict, list]] = []

    def flush() -> None:
        nonlocal inserted_total, skipped_chunks
        if not segment:
            return
        inserted, already = ingest_segment(segment, store, embedder, force=force)
        inserted_total += inserted
        skipped_chunks += already
        log.info(
            "segment: %s articles -> %s chunks upserted, %s already present "
            "(running total %s)",
            len(segment),
            inserted,
            already,
            inserted_total,
        )
        segment.clear()

    for article in iter_articles_resilient(profile):
        seen_articles += 1

        # The held-out slice is never ingested, at any corpus size. Questions
        # built from these articles are unanswerable BY CONSTRUCTION, which is
        # what keeps out-of-corpus eval cases valid as the corpus grows - a
        # hardcoded list of "questions this corpus cannot answer" expires the
        # moment the corpus does.
        if is_held_out(article["id"]):
            held_out += 1
            continue

        prepared = prepare_article(article)
        if prepared is None:
            continue
        segment.append(prepared)

        if len(segment) >= SEGMENT_ARTICLES:
            flush()

    flush()  # tail

    summary = (
        f"Ingestion complete: {inserted_total} chunks upserted, "
        f"{skipped_chunks} chunks already present (of {seen_articles} articles "
        f"seen); {held_out} articles held out (1 in {HOLDOUT_MODULUS}, never "
        f"ingested)"
    )
    log.info(summary)
    # Also to stdout: a detached run is read from its nohup log, and a summary
    # that only reaches Prefect's handler leaves an operator unable to tell a
    # finished run from a killed one. This is the line to grep for.
    print(summary, flush=True)
    return inserted_total


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a Wikipedia profile into Qdrant.")
    parser.add_argument("--profile", default=None, help="tiny | real (default: PROFILE env)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-embed and overwrite chunks that are already stored. Needed when "
        "the embedding model or chunk size changes, since the point IDs do not.",
    )
    args = parser.parse_args()
    ingest_flow(args.profile, force=args.force)
