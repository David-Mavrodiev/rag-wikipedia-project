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

### 1. Start services
```bash
docker compose up --build -d
```

### 2. Pull the LLM (first run only, ~2 GB download)
```bash
make pull-model
```
The Ollama healthcheck only proves the server is up — generation fails with 503 until the model is pulled. Weights persist in the `ollama_data` volume, so this is a one-time step; the embedding model (~130 MB) downloads automatically on first use and is cached in the `hf_cache` volume.

### 3. Ingest Wikipedia (tiny profile ~500 articles)
```bash
PROFILE=tiny make ingest
```

### 4. Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Who created Python?"}'
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
Reports recall@5 and MRR on the answerable golden questions and refusal accuracy on the unanswerable ones (`backend/eval/golden.jsonl`). Exits non-zero if recall@5 or refusal accuracy falls below the 0.8 gate.

## Evaluation

The default evaluation is retrieval-only, so it can run quickly in CI without
Ollama:

```bash
make eval
```

It scores a 60-case golden set: 40 answerable Wikipedia questions and 20
unanswerable/private/out-of-corpus questions. The gate requires:

- `recall@5 >= 0.80`
- `refusal_accuracy >= 0.80`

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
| `tiny`  | ~500     | Fast, for development |
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
