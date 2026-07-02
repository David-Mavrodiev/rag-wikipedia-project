# Prompt: Build a RAG System over Wikipedia

Build an end-to-end Retrieval-Augmented Generation system that answers natural-language questions over a Wikipedia subset with **grounded, cited answers**, served by a self-hosted open-source LLM. Deliver in **8 strictly sequential phases**, each independently demoable with passing tests.

## Architecture

All services run under Docker Compose. A shared `core` package is reused by both the API and the Prefect pipeline.

```
Browser (React+Vite) → FastAPI (/query, /health)
                          ├─ Embedder (bge-small-en-v1.5, 384-d)
                          ├─ Qdrant (vector DB, cosine, dim 384)
                          └─ Ollama (llama3.2:3b)

Ingestion (Prefect): download → clean → chunk → embed → upsert → Qdrant
```

## Tech stack (locked)

- **Language/tooling:** Python 3.12, `uv`, `ruff`, `pytest`
- **Corpus:** HF `wikimedia/wikipedia` (EN) — `tiny` ~500 / `real` ~25k articles via PROFILE
- **Embeddings:** `bge-small-en-v1.5` (384-d) behind an `Embedder` interface
- **LLM:** Ollama `llama3.2:3b` behind an `LLM` interface (swappable via config)
- **Vector DB:** Qdrant (Docker), cosine distance, dim 384 (assert dim at startup)
- **Chunking:** token-based ~512 size / ~64 overlap
- **Orchestration:** Prefect flows/tasks
- **Frontend:** React + Vite + TypeScript (ask / answer / sources)
- **Retrieval (v1):** vector-only top-k. Hybrid/reranker = out of scope.

## Source layout

```
projects/rag-wikipedia/
├── docker-compose.yml          # api · frontend · qdrant · ollama
├── Makefile                    # up · ingest · eval · test
├── pyproject.toml / uv.lock
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, routes, health
│   │   ├── api/                # routers: query, health
│   │   ├── core/               # config, chunking, embeddings, vectorstore, llm, retrieval, prompt
│   │   └── models/             # pydantic schemas
│   ├── pipeline/               # Prefect flows, tasks, sources
│   ├── eval/                   # golden.jsonl, metrics, run_eval.py
│   └── tests/
├── frontend/                   # QueryBox, AnswerView, CitationList
└── docs/runbook.md
```

## What to implement, by phase (each with its tests)

### Phase 1 — Scaffolding & infra
Repo skeleton; Docker Compose with Qdrant + Ollama healthy; FastAPI `GET /health`; config, logging, test harness.
- **Tests:** `/health` returns 200; config parsing unit test.
- **Done:** `docker compose up` → /health green; `make test` passes.

### Phase 2 — Ingestion pipeline (Prefect)
Idempotent flow download → clean → chunk → embed → upsert. Use **deterministic point IDs** (hash of source + chunk index) so re-runs don't duplicate and can resume.
- **Tests:** unit tests for clean/normalize and chunking; pipeline test on a fixture corpus asserting **idempotency** (re-run = same vector count) and resumability.
- **Done:** `make ingest PROFILE=tiny` populates Qdrant; re-run is a no-op.

### Phase 3 — Retrieval layer
Embed query → Qdrant top-k → map to source metadata → assemble context within a token budget.
- **Tests:** integration test (Qdrant + tiny fixture) returns relevant chunks for known questions; token-budget truncation unit test.

### Phase 4 — Generation
Grounded prompt with citation markers; Ollama call; **refusal path** when context is weak ("I don't know"); citation extraction.
- **Tests:** prompt-assembly and citation-formatting unit tests (mocked LLM); no-context input yields a refusal.

### Phase 5 — API
`POST /query → {answer, citations[]}`; pydantic request/response models; input validation (empty/oversized/non-English); error mapping when deps are down.
- **Tests:** integration test of the query path (mocked/small LLM); validation and dependency-failure error cases.
- **Done:** `curl POST /query` returns grounded answer + citations.

### Phase 6 — Web app (React)
Minimal Vite+TS app: ask a question, view answer + expandable citations; loading/error states.
- **Tests:** component tests for QueryBox/AnswerView/CitationList; end-to-end demo via Compose.

### Phase 7 — Evaluation harness
Curate `golden.jsonl`; compute retrieval metrics (**recall@k, MRR**) and answer **groundedness** (optional LLM-as-judge); save a comparable report.
- **Tests:** metric functions unit-tested on synthetic inputs.
- **Done:** `make eval` outputs a metrics report.

### Phase 8 — Packaging & docs
Finalize Compose wiring + per-service healthchecks; README quickstart; runbook; Makefile targets.
- **Done:** a new user goes clone → working demo via the README.

## Acceptance criteria

- `docker compose up` brings all services up healthy on a clean machine.
- Ingestion is end-to-end and **idempotent/resumable**.
- `/query` returns grounded answers **with citations**; declines when no relevant context.
- LLM and embedding model are **swappable via config**.
- Eval harness outputs recall@k, MRR, and groundedness.
- Unit tests cover core logic; integration tests cover the query path; all pass locally.
- README + runbook document setup, ingestion, querying, evaluation.

## Constraints

- No fine-tuning, no auth/billing, no cloud/K8s, no streaming ingestion, no hybrid search/reranking in v1.
- Use the `tiny` profile throughout for fast iteration; `real` profile is a final pass.
