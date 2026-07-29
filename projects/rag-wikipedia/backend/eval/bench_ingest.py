"""Ingestion performance benchmark.

Streams the first N articles of the `real` Wikipedia profile through the real
pipeline components (clean -> chunk -> embed -> upsert), timing each stage, then
extrapolates to the full `real` profile (25k articles).

Usage:
    uv run python backend/eval/bench_ingest.py [N]     # default N=2000

Writes to a throwaway collection (BENCH_COLLECTION, default "bench_ingest") and
deletes it at the end, so the primary `wikipedia` collection is untouched.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# make `app` and `pipeline` importable (this file lives in backend/eval)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.chunking import chunk_text  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.embeddings import BGEEmbedder  # noqa: E402
from app.core.vectorstore import QdrantStore  # noqa: E402
from pipeline.sources import iter_articles  # noqa: E402
from pipeline.tasks import clean_text  # noqa: E402

REAL_TARGET = 25_000  # articles in the `real` profile


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    collection = os.environ.get("BENCH_COLLECTION", "bench_ingest")
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")

    print(f"[bench] N={n} collection={collection} qdrant={qdrant_url}", flush=True)

    t = time.perf_counter()
    embedder = BGEEmbedder(model_name=settings.embed_model)
    model_load_s = time.perf_counter() - t
    print(f"[bench] model loaded in {model_load_s:.1f}s", flush=True)

    store = QdrantStore(url=qdrant_url, collection=collection)
    try:
        store._client.delete_collection(collection)
    except Exception:
        pass
    store.ensure_collection(dim=384)

    t_fetch = t_clean = t_chunk = t_embed = t_upsert = 0.0
    n_articles = n_skipped = n_chunks = 0

    gen = iter_articles("real")
    wall0 = time.perf_counter()
    while n_articles + n_skipped < n:
        tf = time.perf_counter()
        try:
            article = next(gen)
        except StopIteration:
            break
        t_fetch += time.perf_counter() - tf

        tc = time.perf_counter()
        cleaned = clean_text(article["text"])
        t_clean += time.perf_counter() - tc
        if len(cleaned) < 100:
            n_skipped += 1
            continue

        tch = time.perf_counter()
        chunks = chunk_text(cleaned, source_id=article["id"])
        t_chunk += time.perf_counter() - tch
        if not chunks:
            n_skipped += 1
            continue

        te = time.perf_counter()
        vectors = embedder.embed_batch([c.text for c in chunks])
        t_embed += time.perf_counter() - te

        tu = time.perf_counter()
        points = [
            {
                "id": c.point_id,
                "vector": v,
                "payload": {
                    "text": c.text,
                    "source_id": c.source_id,
                    "chunk_index": c.chunk_index,
                    "title": article["title"],
                },
            }
            for c, v in zip(chunks, vectors)
        ]
        store.upsert_batch(points)
        t_upsert += time.perf_counter() - tu

        n_articles += 1
        n_chunks += len(chunks)

        if n_articles % 200 == 0:
            el = time.perf_counter() - wall0
            print(
                f"[bench] {n_articles} art | {n_chunks} chunks | {el:.0f}s "
                f"| {n_articles / el:.1f} art/s | {n_chunks / el:.0f} chunks/s",
                flush=True,
            )

    wall = time.perf_counter() - wall0
    seen = n_articles + n_skipped

    def pct(x: float) -> str:
        return f"{100 * x / wall:5.1f}%" if wall else "  n/a"

    print("\n=========== INGESTION BENCHMARK ===========", flush=True)
    print(f"articles streamed : {seen}  (embedded {n_articles}, skipped {n_skipped})")
    print(f"chunks upserted   : {n_chunks}  ({n_chunks / max(n_articles,1):.1f} chunks/article)")
    print(f"model load        : {model_load_s:6.1f}s (one-time)")
    print(f"wall (pipeline)   : {wall:6.1f}s")
    print("--- stage breakdown ---")
    print(f"fetch (HF stream) : {t_fetch:6.1f}s  {pct(t_fetch)}")
    print(f"clean             : {t_clean:6.1f}s  {pct(t_clean)}")
    print(f"chunk             : {t_chunk:6.1f}s  {pct(t_chunk)}")
    print(f"embed (BGE/CPU)   : {t_embed:6.1f}s  {pct(t_embed)}")
    print(f"upsert (qdrant)   : {t_upsert:6.1f}s  {pct(t_upsert)}")
    print("--- throughput ---")
    print(f"{seen / wall:.1f} articles/s | {n_chunks / wall:.0f} chunks/s")
    if seen:
        est = model_load_s + wall / seen * REAL_TARGET
        print(
            f"--- extrapolation to real ({REAL_TARGET} articles) ---\n"
            f"~{est / 60:.1f} min total (~{wall / seen * REAL_TARGET / 60:.1f} min pipeline + "
            f"{model_load_s:.0f}s model load), ~{n_chunks / seen * REAL_TARGET:,.0f} chunks"
        )

    store._client.delete_collection(collection)
    print(f"[bench] dropped throwaway collection '{collection}'", flush=True)


if __name__ == "__main__":
    main()
