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

### 3. Ingest Wikipedia (tiny profile)
```bash
PROFILE=tiny make ingest
```

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
make eval
```
See [Evaluation](#evaluation) below for what it measures and which gates it
enforces — kept in one place so the two descriptions cannot drift apart.

## Evaluation

The default evaluation is retrieval-only, so it can run quickly in CI without
Ollama:

```bash
make eval
```

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

**The suites must match the ingested corpus.** They target the 60 articles of
the `tiny` profile and every `expected_titles` entry is an article that really
exists in it. Running them against a different profile measures nothing useful:
an earlier version of these suites asked about the speed of light and the
telephone, which have no article in `tiny`, and still scored `recall@5 = 1.000`
because the expectation was matched as a substring of the retrieved text.

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

## Profiles
| Profile | Articles | Notes |
|---------|----------|-------|
| `tiny`  | ~60 articles / ~1k chunks | Fast, for development. The eval suites target this profile. |
| `real`  | ~25 000  | Full quality pass |

## Swapping models
Set environment variables before starting:
- `EMBED_MODEL` — any SentenceTransformers model (default: `BAAI/bge-small-en-v1.5`)
- `LLM_MODEL` — any Ollama model tag (default: `llama3.2:3b`)

## Architecture
```
Browser → FastAPI → [Embedder → Qdrant → LLM] → cited answer
Prefect pipeline: HF Wikipedia → clean → chunk → embed → Qdrant
```
