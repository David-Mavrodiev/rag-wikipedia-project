# Ingestion Performance — Large Dataset Analysis

Analysis of the ingestion pipeline (`download → clean → chunk → embed → upsert`)
under the `real` profile (25,000 Wikipedia articles), why it does not scale as-is,
and the changes required to make it feasible.

## TL;DR

- Embedding on CPU is **96.9%** of ingestion time and is the sole bottleneck.
- Measured throughput: **~0.5 articles/s (~7.4 chunks/s)**.
- Extrapolated full `real` run: **~15 hours** and **~404,000 vectors** on CPU.
- The pipeline is **sequential, single-threaded, small-batch, and non-resumable**,
  so a 15-hour run has no way to recover from an interruption.
- Highest-leverage fixes: **GPU or ONNX/quantized embeddings**, **larger batches
  across articles**, **batched upserts**, and **resumable/idempotent skipping**.

## How this was measured

A benchmark harness ([../backend/eval/bench_ingest.py](../backend/eval/bench_ingest.py))
streams the first N articles of the `real` profile through the real pipeline
components and times each stage individually, writing to a throwaway Qdrant
collection (so the primary `wikipedia` collection is untouched).

```
uv run python backend/eval/bench_ingest.py 150
```

Two independent samples (N=150 complete run, and an N=200 checkpoint from a larger
run) agreed at ~0.5 articles/s, so the numbers below are stable.

## Measured results (N = 150 articles)

| Stage | Time | Share |
|---|---:|---:|
| fetch (HF stream) | 5.1 s | 1.6% |
| clean | 0.2 s | 0.1% |
| chunk | 0.9 s | 0.3% |
| **embed (BGE / CPU)** | **315.5 s** | **96.9%** |
| upsert (Qdrant) | 3.8 s | 1.2% |
| **pipeline wall** | **325.5 s** | 100% |
| model load (one-time) | 14.2 s | — |

- **Throughput:** ~0.5 articles/s · ~7.4 chunks/s
- **Fan-out:** 16.1 chunks/article (512-token chunks, 64-token overlap)

## Extrapolation to the full `real` profile (25,000 articles)

| Metric | Value |
|---|---:|
| Pipeline time | ~904 min (**~15 hours**) |
| Vectors produced | ~404,000 |
| Embedding portion | ~14.6 hours |

At `real` scale (25k articles) the model-load cost is negligible; wall time is
essentially the embedding time.

## Issues with large-dataset ingestion

### 1. CPU-bound embedding dominates (root cause)
`BGEEmbedder` runs `sentence-transformers` on CPU
([../backend/app/core/embeddings.py](../backend/app/core/embeddings.py)), and the
backend image is built CPU-only by design
(`ENV UV_TORCH_BACKEND=cpu` in [../backend/Dockerfile](../backend/Dockerfile)).
At ~7.4 chunks/s, ~404k chunks ≈ 15 hours. Nothing else in the pipeline is close.

### 2. Sequential, single-threaded pipeline
`ingest_flow` processes one article at a time in a Python `for` loop, fully
embedding each article before fetching the next
([../backend/pipeline/flow.py](../backend/pipeline/flow.py)). Download, embed, and
upsert never overlap, and multiple CPU cores are left idle.

### 3. Small embedding batches
`embed_chunks` embeds only one article's chunks per `encode` call (~16 texts)
([../backend/pipeline/tasks.py](../backend/pipeline/tasks.py)). With the
`sentence-transformers` default `batch_size=32`, batches are small and dominated by
per-call overhead; larger batches (256–512) use CPU/GPU far more efficiently.

### 4. No resumability — the killer for long runs
A re-run streams from the start and **re-embeds every article**. Deterministic
point IDs make the *upsert* idempotent
([../backend/app/core/chunking.py](../backend/app/core/chunking.py)), but the
expensive embedding work is repeated. There is no checkpoint and no "skip if
already ingested" step, so any interruption during a 15-hour run means starting
over. (This is exactly why the earlier host sleep/resume crashes were so costly,
and it is already flagged as the main gap in
[ingestion-status-report.md](ingestion-status-report.md).)

### 5. Per-article synchronous upserts
Each article issues its own `PUT /points?wait=true` to Qdrant. Only 1.2% today,
but once embedding is sped up, thousands of small synchronous round-trips become a
real cost; upserts should be batched.

### 6. Orchestration overhead at scale
A 25k run creates ~100k Prefect task runs (4 tasks × 25k). That is significant
scheduling overhead and pressure on Prefect's local SQLite backend (already
observed emitting `database is locked` warnings). Batching reduces task count.

### 7. Fixed chunking inflates the vector count
`CHUNK_SIZE=512` / `CHUNK_OVERLAP=64` yields ~16 chunks/article → ~404k vectors.
Every extra chunk is extra embedding, storage, and query-time work.

## Recommended changes (prioritized)

### Tier 1 — Embedding (biggest lever)
1. **GPU embeddings.** Run the embedder on CUDA (GPU-enabled image + `device="cuda"`).
   Typically **10–50×**, bringing 25k from ~15 h to roughly **20–90 min**.
2. **CPU-optimized inference (if no GPU).** Use ONNX Runtime with an int8-quantized
   `bge-small` (e.g. `optimum`, or Qdrant's `fastembed`). Expect **~2–4×** on CPU.
3. **Batch across articles.** Accumulate chunks into large batches (256–512) and call
   `encode` once per batch instead of once per article. Add `batch_size` to
   `embed_batch` and set it explicitly.

### Tier 2 — Parallelism & batching
4. **Parallelize embedding** across worker processes (CPU: multiprocessing across
   cores; GPU: larger batches / multiple streams).
5. **Producer–consumer pipeline.** Prefetch/stream articles on one worker while
   others embed, so fetch and embed overlap (removes the 1.6% fetch from the
   critical path and keeps embedders saturated).
6. **Batched upserts.** Buffer points and flush every N (e.g. 512) with `wait=false`
   during bulk load, then a final synchronous flush.

### Tier 3 — Resumability & resilience
7. **Skip already-ingested work.** Before embedding, check whether an article's
   deterministic point IDs already exist in Qdrant (or maintain a processed-ID
   ledger) and skip. Makes runs restartable and cheap to resume.
8. **Checkpointing + retries.** Persist progress (last processed article index) and
   add retry/backoff on transient Qdrant/network errors.

### Tier 4 — Reduce total work
9. **Tune chunking.** Larger `CHUNK_SIZE` (e.g. 768–1024) and/or less overlap cuts
   the vector count (and thus embedding + storage + query cost) substantially.
10. **Filter earlier.** Drop stubs/low-value articles before embedding.

### Tier 5 — Orchestration & ops
11. **Batch Prefect tasks.** Operate on batches of articles per task run instead of
    per-article to cut ~100k task runs down by 1–2 orders of magnitude.
12. **Run ingestion as a dedicated job**, not on a laptop prone to sleep; or point
    Prefect at a persistent server/work pool so long runs survive restarts.

## Expected outcome

| Configuration | Est. time for 25k |
|---|---:|
| Current (CPU, per-article, sequential) | ~15 hours |
| CPU + ONNX/int8 + large batches | ~3–6 hours |
| GPU + large batches | ~20–90 min |
| GPU + batches + resumable + batched upserts | ~20–60 min, restart-safe |

The single most important change is moving embedding off per-article CPU inference
(GPU or ONNX/quantized + large batches). Resumability is the second: it turns a
fragile 15-hour job into one that can be interrupted and continued safely.
