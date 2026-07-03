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
- **Chunking:** token-based ~512 size / ~64 overlap, counted with `tiktoken` (`cl100k_base`) — the same tokenizer is used for the retrieval context budget, so all token math agrees
- **Orchestration:** Prefect **3.x** flows/tasks (pin the major version — 2.x and 3.x APIs differ)
- **Frontend:** React + Vite + TypeScript (ask / answer / sources)
- **Retrieval (v1):** vector-only top-k (default k=5), context budget 3000 tokens. Hybrid/reranker = out of scope.

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

Health semantics, decided once: `GET /health` is **liveness only** — process up, no dependency probes. Readiness lives in Compose per-service healthchecks plus `depends_on: service_healthy`; dependency failures surface as 503s from `/query` (Phase 5), never from `/health`.
- **Tests:** `/health` returns 200; config parsing unit test.
- **Done:** `docker compose up` → /health green; `make test` passes.

### Phase 2 — Ingestion pipeline (Prefect)
Idempotent flow download → clean → chunk → embed → upsert. Use **deterministic point IDs** (hash of source + chunk index) so re-runs don't duplicate and can resume.
- **Tests:** unit tests for clean/normalize and chunking; pipeline test on a fixture corpus asserting **idempotency** (re-run = same vector count) and resumability.
- **Done:** `make ingest PROFILE=tiny` populates Qdrant; re-run is a no-op.

### Phase 3 — Retrieval layer
Embed query → Qdrant top-k → map to source metadata → assemble context within a **3000-token budget** (tiktoken `cl100k_base`; the top-1 chunk is always kept, even if it alone exceeds the budget).
- **Tests:** integration test (Qdrant + tiny fixture) returns relevant chunks for known questions; token-budget truncation unit test.
- **Early signal:** run retrieval-only recall@5 against ~10 golden questions at the end of this phase — don't wait until Phase 7 to learn retrieval is broken.

### Phase 4 — Generation
Grounded prompt with inline `[n]` citation markers (context chunks numbered `[1]`, `[2]`, …); Ollama call; **refusal path**; citation extraction (only indices actually cited in the answer become citations).

**Refusal rule (precise):** refuse iff retrieval returns no results **or** the top-1 cosine score < `refusal_threshold` (config, default **0.3**). The refusal answer is the exact string `"I don't know based on the provided context."` with an empty `citations` list; the system prompt also instructs the model to emit that string when the context doesn't contain the answer.
- **Tests:** prompt-assembly and citation-formatting unit tests (mocked LLM); refusal on **empty** retrieval **and** on **irrelevant** context (top-1 below threshold) — the second case is the one that matters in demos.

### Phase 5 — API
`POST /query` with pydantic request/response models; input validation: empty or > 500 chars → 422 (language detection is out of scope — don't attempt it); Qdrant or Ollama down → **503** with a JSON `detail`.

Response contract — the frontend, prompt template, and citation extraction all depend on this shape, so don't improvise it:

```json
{
  "answer": "Python was created by Guido van Rossum [1].",
  "citations": [
    {
      "index": 1,
      "title": "Python (programming language)",
      "source_id": "wiki-12345-chunk-2",
      "excerpt": "…conceived by Guido van Rossum in the late 1980s…"
    }
  ]
}
```
- **Tests:** integration test of the query path (mocked/small LLM); validation and dependency-failure error cases.
- **Done:** `curl POST /query` returns grounded answer + citations.

### Phase 6 — Web app (React)
Minimal Vite+TS app: ask a question, view answer + expandable citations; loading/error states.
- **Tests:** component tests for QueryBox/AnswerView/CitationList; end-to-end demo via Compose.

### Phase 7 — Evaluation harness
Curate `golden.jsonl` with **≥ 20 questions, of which ≥ 5 are unanswerable** from the corpus (expected outcome: refusal). Compute retrieval metrics (**recall@k, MRR**) on the answerable subset and **refusal accuracy** on the unanswerable subset; answer **groundedness** is an optional LLM-as-judge — if used, the judge must be a stronger model than the generator, otherwise treat its scores as advisory only. Save a comparable report.
- **Tests:** metric functions unit-tested on synthetic inputs.
- **Done:** `make eval` outputs a metrics report, and on the tiny profile **recall@5 ≥ 0.8** and **refusal accuracy ≥ 0.8** — a report with no bar is not a gate.

### Phase 8 — Packaging & docs
Finalize Compose wiring + per-service healthchecks; README quickstart; runbook; Makefile targets.

First-run reality — document it, don't hide it: the Ollama healthcheck proves the *server* is up, not that the model is present. Provide `make pull-model` (`docker compose exec ollama ollama pull llama3.2:3b`) as an explicit quickstart step, with weights persisted in the `ollama_data` volume. First run downloads ~2 GB of LLM weights plus the embedding model; the README must say so.
- **Done:** a new user goes clone → working demo via the README.

## Acceptance criteria

- `docker compose up` brings all services up healthy on a clean machine.
- Ingestion is end-to-end and **idempotent/resumable**.
- `/query` returns grounded answers **with citations**; declines when no relevant context.
- LLM and embedding model are **swappable via config**.
- Eval harness outputs recall@k, MRR, refusal accuracy, and groundedness; on the tiny profile, **recall@5 ≥ 0.8** and **refusal accuracy ≥ 0.8**.
- Unit tests cover core logic; integration tests cover the query path; all pass locally.
- README + runbook document setup, ingestion, querying, evaluation.

## Constraints

- No fine-tuning, no auth/billing, no cloud/K8s, no streaming ingestion, no hybrid search/reranking, no input language detection in v1.
- Use the `tiny` profile throughout for fast iteration; `real` profile is a final pass. Budget for it: embed in batches (e.g., 64 chunks per call); ~25k articles on CPU takes **hours, not minutes** — run it once, deliberately.
