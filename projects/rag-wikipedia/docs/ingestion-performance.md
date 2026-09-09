# Ingestion Performance — Large Dataset Analysis

Analysis of the ingestion pipeline (`download → clean → chunk → embed → upsert`)
under the `real` profile (25,000 Wikipedia articles), why it does not scale as-is,
and the changes required to make it feasible.

> **CORRECTION (2026-08-27) — the `real` profile has now actually been run.**
> The projections below were made from an N=150 sample and are wrong by large
> factors in both directions. They are kept because *how* they were wrong is the
> useful part. Read this section first; treat everything after it as the
> original estimate, not as fact.

## What the full 25k run actually produced

| | predicted below | measured |
|---|---:|---:|
| Vectors | ~404,000 | **87,173** |
| Chunks per article | 16.1 | **3.53** |
| Articles ingested | 25,000 | **24,694** (+ ~305 held out by design) |
| Raw vector storage | ~620 MB | **~134 MB** |

**The prediction was 4.6x too high, and the cause is sampling.** The 16.1
chunks/article came from the first 150 articles of the stream — which are
`Anarchism`, `Abraham Lincoln`, `Aristotle` and similar. Wikipedia dumps are not
randomly ordered: substantial articles cluster at the start. Density collapses
with depth:

| stream position | chunks per streamed article |
|---|---:|
| 0 – 1,999 | 11.09 |
| 2,000 – 3,999 | 5.34 |
| 4,000 – 5,999 | 2.75 |
| 6,000 – 7,999 | 2.73 |
| 8,000 – 9,999 | 2.54 |
| 10,000 – 11,999 | 2.37 |

It stabilises near 2.4. Extrapolating any per-article quantity from the head of
this stream overestimates it by 4-5x. **Any future sample must be taken from a
random or deep slice, never from the first N.**

## Why the GPU did not deliver the promised 10-50x

Tier 1 below predicts GPU embedding brings 25k "from ~15 h to roughly 20-90 min".
The GPU was fitted (`torch 2.12.1+cu126`, RTX 3060 Laptop 6 GB) and it did not.
Measured on this machine:

| condition | GPU clock | throughput |
|---|---|---:|
| micro-benchmark, cold GPU, fp16, batch 64 | ~1400 MHz | **69.8 chunks/s** |
| full pipeline, cold, per-article | 652 MHz | **24 chunks/s** |
| full pipeline, heat-soaked, per-article | **210 MHz** | **1 chunk/s** |
| full pipeline, heat-soaked, batched | **210 MHz** | **5-24 chunks/s** |

Three effects compound, and none of them is the GPU being slow:

**1. Thermal throttling costs an order of magnitude.** The card clamps to
**210 MHz of a 2,100 MHz nominal clock** at 93-97 °C — roughly a 10x reduction.
It draws only ~24-30 W of its ~95 W budget at that temperature, and it *idles*
at 89-91 °C, so the limit is the chassis cooling, not the silicon. A benchmark
that runs for seven seconds never reaches this state; the one below did not, and
that is why it predicted 69.8 chunks/s for work that sustained far less.

**2. The machine shut down four times.** Windows logged Event ID 41 (unclean
shutdown) three times in 24 h. This is thermal protection, not a fault. See
[PERTINENT.md](../../../PERTINENT.md) for the recovery procedure. Total wall time
is therefore not meaningful — the run spans several sessions.

**3. The bottleneck moved.** The analysis below is correct that embedding is
96.9% of CPU-bound ingestion. Once embedding moved to the GPU *and* article
density fell to ~2.4 chunks, the fixed per-article cost — one Qdrant existence
check, one upsert round-trip, four Prefect task runs — became dominant.
Throughput fell to **1 chunk/s** while the GPU sat at 0-12% utilisation.
Batching articles into segments of 200 (one existence check, one embed call, one
upsert block, one task run) restored it to **5-24 chunks/s at the same
temperature**: a 5-24x improvement from removing overhead, not from faster
hardware.

## What actually made the run finish

Not speed. Three properties:

- **Resumability** (`existing_ids` before embedding). Across four thermal
  shutdowns, **not one chunk was re-embedded**. Item 7 in Tier 3 below, and the
  single most valuable change in this document.
- **Batching**, as above.
- **Retry with backoff on the stream.** A HuggingFace CDN 503 killed one run 90
  seconds in, before this existed.

## Corrected guidance

- **Do not size storage or time from a head-of-stream sample.**
- **Do not benchmark embedding throughput in short bursts** on a thermally
  constrained machine. Measure under sustained load, or measure the clock.
- On this hardware, **cooling is the binding constraint on every compute-heavy
  task**, and no code change recovers a 10x clock deficit. The realistic fix is
  off-box compute; the realistic mitigation is a duty cycle, since running flat
  out keeps the card at its slowest state.
- The Tier 1 "GPU → 20-90 min" row remains true *for adequately cooled
  hardware*. It was never true for this laptop.

---

## TL;DR

- Embedding on CPU is **96.9%** of ingestion time and is the sole bottleneck.
- Measured throughput: **~0.5 articles/s (~7.4 chunks/s)**.
- Extrapolated full `real` run: **~15 hours** and **~404,000 vectors** on CPU.
  *(SUPERSEDED: the real run produced **87,173** vectors — see the correction above.)*
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
  *(SUPERSEDED: 16.1 is an artefact of sampling the first 150 articles; the
  true figure over 24,694 articles is **3.53**.)*

## Extrapolation to the full `real` profile (25,000 articles)

| Metric | Value |
|---|---:|
| Pipeline time | ~904 min (**~15 hours**) *(not comparable: the real run was GPU-bound, thermally throttled and interrupted four times)* |
| Vectors produced | ~404,000 *(actual: **87,173**)* |
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
*(SUPERSEDED: ~87k chunks, not 404k. And once embedding moved to the GPU, the
per-article overhead this paragraph dismisses became the bottleneck — see the
correction at the top.)*

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

### 4. No resumability — the killer for long runs — **FIXED**
A re-run used to stream from the start and **re-embed every article**.
Deterministic point IDs made the *upsert* idempotent
([../backend/app/core/chunking.py](../backend/app/core/chunking.py)), but the
expensive embedding work was repeated, so any interruption during a 15-hour run
meant starting over.

`ingest_flow` now checks each article's point IDs against Qdrant before
embedding ([../backend/pipeline/flow.py](../backend/pipeline/flow.py) via
`QdrantStore.existing_ids`). Chunks already stored are skipped; a partially
ingested article re-embeds only its missing chunks. A re-run therefore costs one
cheap ID lookup per article instead of the whole embedding bill, and an
interrupted run continues where it stopped.

`make ingest-force` re-embeds regardless — required when `EMBED_MODEL` or the
chunk size changes, because the point IDs stay the same while the vectors they
should hold do not.

The original text is kept below for the record. (This is exactly why the earlier host sleep/resume crashes were so costly,
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
*(SUPERSEDED: 3.53 chunks/article → 87,173 vectors. The chunking parameters are
unchanged; the 16.1 figure was a sampling artefact, so this item is far less
material than it appeared.)*
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
