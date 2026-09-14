# RAG Wikipedia

End-to-end Retrieval-Augmented Generation over Wikipedia, powered by:
- **Qdrant** (vector search)
- **Ollama + llama3.2:3b** (generation)
- **bge-small-en-v1.5** (embeddings)
- **FastAPI** (backend)
- **React + Vite** (frontend)

## Quickstart

### Prerequisites
- Docker + Docker Compose
- 8 GB RAM (for model + embeddings)

### Configuration

All settings have working defaults, so no configuration is required to run
locally. To override any of them, copy `backend/.env.example` to `backend/.env`
and edit it - that file documents every setting, its default, and its allowed
range. Two are worth knowing about up front:

- `RATE_LIMIT_ENABLED` (default `true`) requires Redis; `POST /query` answers
  503 without it. Set it to `false` for a single-machine demo.
- `QUALITY_ADMIN_TOKEN` (default empty) gates the auto-correcting audit, which
  rewrites and persists the refusal thresholds. Empty disables that path.

### 1. Start services
```bash
docker compose up --build -d
```

### 2. Pull the LLM (first run only, ~2 GB download)
```bash
make pull-model
```
The Ollama healthcheck only proves the server is up — generation fails with 503 until the model is pulled. Weights persist in the `ollama_data` volume, so this is a one-time step; the embedding model (~130 MB) downloads automatically on first use and is cached in the `hf_cache` volume.

### 3. Ingest Wikipedia

```bash
PROFILE=tiny make ingest   # 500 articles, streamed from Hugging Face
```

This fills the `wikipedia` collection, which is the one the API serves, so it is
what steps 4 and 5 below query. `PROFILE=real` ingests 25,000 articles instead -
hours of embedding, and resumable (see **Profiles**).

To *measure* rather than query, ingest the committed fixture instead - see
[Evaluation](#evaluation). The two corpora live in different collections on
purpose.

### 4. Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Who was Abraham Lincoln?"}'
```

Or open http://localhost:5173 in your browser.

### 5. Run tests
```bash
make test
```

### 6. Run evaluation

```bash
make eval-fixture-all      # ingest the committed fixture, then score it
```

See [Evaluation](#evaluation) below for what it measures and which gates it
enforces — kept in one place so the two descriptions cannot drift apart. It
exits non-zero today, and is supposed to: two gates are red on purpose.

## Evaluation

The default evaluation is retrieval-only, so it can run quickly in CI without
Ollama:

```bash
make eval-corpus       # ingest the committed 150-article fixture (no network)
make eval-fixture      # score the suites against it
# or both at once:
make eval-fixture-all
```

**Use the pinned targets, not bare `make eval`.** `make eval` scores
`settings.collection`, which defaults to the working `wikipedia` collection -
right when you want to score whatever you have ingested, wrong here, because the
committed suites describe the fixture corpus in `wikipedia_eval`. Running
`make eval-corpus && make eval` scores one corpus with another corpus's
expectations and reports confident, meaningless numbers. `eval-fixture` pins the
profile and the collection together so that cannot happen.

The suites and the gates it enforces:

<!-- docs-check:begin eval-suites -->
| suite | cases | answerable | unanswerable |
|---|---:|---:|---:|
| `golden.jsonl` | 60 | 40 | 20 |
| `holdout.jsonl` | 20 | 15 | 5 |
| `adversarial.jsonl` | 20 | 10 | 10 |

Gates enforced (all unconditional):

- `recall@k >= 0.8`
- `precision@k >= 0.6`
- `refusal_accuracy >= 0.8`
- `false_accept_rate <= 0.1`
<!-- docs-check:end -->

All four gates are unconditional — a missing subset cannot silently skip one,
because `validate_golden_set` rejects an incomplete suite up front.

**The suites must match the ingested corpus.** They target the **`fixture`
profile** - the 150 articles committed as `backend/eval/fixtures/corpus_eval.jsonl.gz`
and ingested by `make eval-corpus` into `wikipedia_eval` - and every
`expected_titles` entry is an article that really exists in it. Running them
against a different corpus measures nothing useful, in both directions:

- An earlier version asked about the speed of light and the telephone, which no
  article in the small dev corpus covered, and still scored `recall@5 = 1.000`
  because the expectation was matched as a *substring* of the retrieved text.
- Scored against the 24,694-article `real` corpus, those same questions become
  *answerable*, so the out-of-corpus cases would report a confident 1.000 for a
  system that does not refuse them at all.

That second failure is why a served corpus gets its own generated suite - see
**Evaluating the corpus you actually serve** below.

Each unanswerable subset deliberately mixes two kinds of case: questions with a
personal or time-bound trigger word, which the intent filter catches, and
ordinary questions this corpus simply cannot answer, which only the *evidence*
gate can catch. Without the second kind, refusal accuracy grades the keyword
list against itself.

Each run writes:

- `backend/eval/report.json` for machine-readable metrics and per-question diagnostics.
- `backend/eval/report.md` for a reviewer-friendly failure analysis.

For slower generation-quality checks, run:

```bash
make eval-groundedness
```

Groundedness is reported only; retrieval and refusal remain the quality gates.

To check for golden-set overfitting, run the audit suite:

```bash
make eval-audit
```

The audit compares the visible golden set against holdout and adversarial sets,
flags large golden-vs-holdout gaps, and writes `backend/eval/audit_report.json`
plus `backend/eval/audit_report.md`. While the API is running, internal operators
can call `POST /quality/audit` to refresh the runtime quality state exposed by
`GET /quality`.

`audit_report.json` records **provenance** - the git sha, profile, collection and
vector count it measured. `GET /quality` refuses to report those numbers when the
deployment serves a different collection, and `make audit-freshness` fails when a
file that can move the metrics has changed since the report was generated. A
published number should say what it measured, and go stale loudly rather than
quietly.

### Evaluating the corpus you actually serve

The committed suites describe the 150-article fixture and are the reproducible CI
baseline: identical on every machine, so a metric change means the *code* changed.
They cannot describe a large served corpus, because their out-of-corpus questions
stop being out of corpus once it grows.

So a served corpus gets its own generated suite:

```bash
uv run python scripts/build_serving_suite.py --collection wikipedia
```

Unanswerable cases are drawn from the **held-out slice**, so they stay unanswerable
at any corpus size and there is no list to maintain. The generator's rule is
*verify corpus properties, never system behaviour* - one that kept only the cases
retrieval already refuses would score 1.0 by construction and measure nothing.

Measured on 24,694 articles / 87,173 vectors, against the 150-article fixture:

| metric | fixture | serving | reading |
|---|---:|---:|---|
| `recall@5` | 1.000 | 0.933 | retrieval scales |
| `mrr` | 1.000 | 0.904 | still ranked first, usually |
| `precision@5` | 0.930 | 0.383 | mostly the metric's shape at k=5 with one expected title |
| `false_accept_rate` | 0.500 | 0.600 | **worse, and worsening with size** |

**Retrieval survives a 165x corpus increase; refusal does not.** `false_accept_rate`
rises monotonically with corpus size - 0.450 at 60 articles, 0.500 at 500, 0.600 at
24,694 - because `refusal_min_overlap_terms = 1` accepts on a single shared token, so
every extra article is another chance for an irrelevant chunk to supply it. The gate
weakens exactly as the corpus grows. A 4x3 threshold sweep found no setting that
clears the gates without also refusing *"Who was Abraham Lincoln?"*, so the fix is
evidence scoring that weighs which terms overlap - not a threshold. **The quality
gates are red on purpose and have not been relaxed to hide it.**

**What that number measures.** `false_accept_rate` is retrieval-only: it scores the
decision `retrieve()` makes, before any model runs. Reconstructed case by case, the
evidence gate refused *nothing* on the fixture suites - every retrieval-level refusal
came from the intent filter, and the model made every genuine out-of-corpus call.
That makes the current safety behaviour a property of one model: [docs/retrieval-only-vs-end-to-end.md](docs/retrieval-only-vs-end-to-end.md).
Since 2026-09-12 the gate is enabled at 0.45, measured on the serving corpus:
`false_accept_rate` 0.600 -> 0.500 there, and 0.500 -> 0.400 on golden and
adversarial at no cost in false refusals.

## Profiles

| Profile | Articles | Source | Notes |
|---------|---------|--------|-------|
| `fixture` | 150 / 2,422 vectors | committed `.jsonl.gz` | No network, deterministic. **What the eval suites target and what CI ingests.** `make eval-corpus` |
| `tiny` | 500 | streamed from Hugging Face | Fast dev index to query by hand |
| `real` | 25,000 | streamed from Hugging Face | Full pass. Measured: 24,694 ingested, 87,173 vectors, ~305 held out by design |

Ingestion is **resumable and idempotent**: point IDs are a SHA-256 of
`source_id::chunk_index`, and each segment asks Qdrant which IDs it already
holds and embeds only what is missing. Re-running after an interruption costs
one lookup per segment, not the whole embedding bill - the `real` run above
survived four machine shutdowns without re-embedding a single chunk. Pass
`--force` only when the embedding model or the chunking parameters change,
since the IDs stay the same while the vectors they should hold do not.

**1 article in 100 is held out and never ingested** (`app.core.holdout`,
`sha256(article_id) % 100 == 0`). Questions about those articles are
unanswerable by construction at any corpus size, which is what keeps
out-of-corpus eval cases valid as the corpus grows.

## Swapping models
Set environment variables before starting:
- `EMBED_MODEL` — any SentenceTransformers model (default: `BAAI/bge-small-en-v1.5`)
- `LLM_MODEL` — any Ollama model tag (default: `llama3.2:3b`)

## Architecture
```
Browser → FastAPI → [Embedder → Qdrant → LLM] → cited answer
Prefect pipeline: HF Wikipedia → clean → chunk → embed → Qdrant
```
