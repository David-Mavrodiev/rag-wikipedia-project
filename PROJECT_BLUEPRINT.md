# RAG over Wikipedia — Project Blueprint

> **Purpose.** How this project is built, and *why* each piece is the way it is: the architecture, the pins, the evaluation design, the hard-won lessons, and a map of every file. It doubles as a **reusable template** for similar RAG/GenAI freelance missions. **git is the source of truth for the code** (§16); this document explains it rather than copying it.
>
> **How to use it.** Read §1–§4 for the shape and §6 for the map of files. §12 (Hard-won lessons) is the part that took the longest to discover — read it before you debug anything.
>
> *Changed 2026-09-11. This file used to embed every source file verbatim, as a rebuild-from-scratch copy. By that date 23 of its 24 embedded files had drifted from the repository and 25 newer files had never been embedded, with nothing checking any of it. §6 is now a map that points at git, so it can no longer quietly disagree with the code it describes.*

---

## 1. What it is

A black-box, **eval-first** Retrieval-Augmented Generation service. It answers natural-language questions over a **Wikipedia subset** with **grounded, cited answers**, served by a **self-hosted open-source LLM**. It **declines** ("I don't know…") when the retrieved context doesn't support an answer — reliability over completeness.

**In scope (v1):** ingestion → retrieval → grounded generation with citations → evaluation → API → local Docker + Azure deployment.
**Out of scope (v1):** hybrid search, reranking, LangChain/LlamaIndex, agents, auth/billing, streaming ingestion.

> **SCOPE EXTENSION (2026-09-08) — v2 adds agents and a second cloud, and does
> not retract the v1 decision.** The two lines above stand as written. The
> exclusion was correct when it was made, and the reasoning is worth keeping:
> a framework buys nothing for a single-shot chain — retrieve, build a prompt,
> generate, check the citations — while costing the property this project
> exists to demonstrate, which is that every step is explicit, readable and
> unit-tested.
>
> What changed is the shape of the problem, not that judgement. A bounded
> retrieve → grade → rewrite → verify loop carrying checkpointed state is real
> orchestration, and that is where a graph runtime earns its place. So the
> agent runtime becomes a **registry**, on the pattern already established for
> the LLM and embedder in §17: `direct` — the v1 chain, still the default and
> still framework-free — and `langgraph` as a peer entry, chosen by one value.
> The nodes stay framework-free functions in `app/core/`; LangGraph supplies
> the wiring, the state and the checkpointer, never the logic. The cloud gets
> the same treatment: GCP joins Azure as a deployment target rather than
> replacing it.
>
> Enforced rather than asserted: `backend/tests/test_layering.py` parses every
> module under `app/` and fails if it imports anything outside an allowlist of
> base packages. An allowlist rather than a denylist on purpose — a denylist
> only catches what someone thought to forbid, while an allowlist fails on
> anything new and forces the addition into a reviewed diff. A further test
> refuses to let the allowlist itself be widened with a framework or a cloud
> SDK, because the cheapest way to silence the first test would otherwise be to
> add the offender to it. A last one states the dependency direction — `app/`
> never imports `engines/` — before `engines/` exists, which is the moment to
> state it.
>
> Verified the only way such a test is worth anything, by watching it fail: a
> `from google.cloud import bigquery` placed inside a function body, so the
> module still imported cleanly and every other test still passed. It was
> caught, and named the file.
>
> *Built 2026-09-09 on `feat/agnostic-engines`, and measured against `direct` on
> 2026-09-10 — see §8 for the head-to-head, which the framework did not win.*

---

## 2. Architecture

```
                       Browser (React + Vite)
                                │  POST /query          GET /quality
                                ▼                            │
                      RateLimitMiddleware ──► Redis          │
                        (token bucket, ASGI)                 │
                                │  429 / 503                 │
                                ▼                            ▼
              FastAPI  (/query, /health, /quality, /quality/audit)
                                │
        ┌───────────────┬───────┴────────┬──────────────────┐
        ▼               ▼                ▼                  ▼
   Embedder        Qdrant           (context           Ollama
 bge-small-384   cosine, top-k    budgeted, tokens)  llama3.2:3b
        │               │                                  │
        └── query embed ┘                                  ▼
                                              answer + [n] citations
                                              (or controlled refusal)

Ingestion (Prefect, offline):
  wikimedia/wikipedia → clean → chunk → embed → upsert → Qdrant
  (deterministic point IDs = idempotent / resumable)
```

<!-- docs-check:begin services -->
Compose runs **5 services**: `redis`, `qdrant`, `ollama`, `api`, `frontend`.
<!-- docs-check:end -->

**Query path:** rate-limit check (Redis token bucket; **503 if Redis is
unreachable**, so Redis is a hard dependency of `/query` unless
`RATE_LIMIT_ENABLED=false`) → **intent filter**: a personal or time-bound
question is refused before retrieval runs → embed question → Qdrant top-k
(cosine) → **evidence gate** `decide_evidence`: refuse on no results, on a
top score below the minimum, or on insufficient term overlap with the retrieved
text; accept on sufficient overlap or a high-confidence vector match → assemble
context within a token budget → prompt the LLM with inline `[n]` citation
markers → extract the indices the model actually cited → return
`{answer, citations[], refused}`.

The refusal decision therefore has **three independent layers**: the intent
filter (before retrieval), the evidence gate (after retrieval), and the model
itself (which can decline evidence retrieval accepted). `refused` in the API
response is the retrieval decision *or* a model refusal; the eval harness
reports them separately, because they disagree — see §8.

**Quality path:** `GET /quality` serves the last audit verdict from an
in-process singleton, falling back to `eval/audit_report.json`.
`POST /quality/audit` re-scores every suite on a worker thread under a lock
(409 if one is running); `?auto_correct=true` rewrites and persists the five
retrieval thresholds and requires `X-Quality-Token`. nginx refuses that path at
the edge in deployed stacks.

**Engine layer.** `backend/engines/` holds an `Engine` interface — one question
in, one grounded-or-refused `EngineResult` out — and a registry selected by one
value, `ENGINE_CHOICE`, on the same name → factory pattern as the dormant
LLM/embedder registry in §17. Two engines are registered: `direct`, the v1 chain
above expressed as an engine, and `langgraph`, a graph that grades the evidence,
can reword the question and retry within a budget, and verifies citations before
answering. The contract is that an engine *orchestrates* `app/core` and never
reimplements it, so both share one definition of evidence, refusal and grounding.
`/query` does not dispatch through the registry yet: the serving path is still the
v1 handler, and `tests/test_engine_conformance.py` asserts that `direct` returns
exactly what `/query` returns. Today the registry is used by the eval harness (§8).

**Three contracts, enforced by tests rather than stated:**
- `LLM` must decode deterministically (temperature 0, pinned seed), so an answer
  is reproducible and two engines are comparable.
- `Embedder` must report a *probed* `dim`; `ensure_collection(dim)` refuses a
  collection created at a different size, naming both.
- `app/` imports no agent framework and no cloud SDK: `tests/test_layering.py`
  checks it against an allowlist of base packages, and refuses any attempt to
  widen that allowlist with a framework.

---

## 3. Tech stack — exact versions & pins (and WHY)

| Concern | Choice | Why / pin note |
|---|---|---|
| Language | Python **3.12** | typed backend |
| Pkg manager | **uv** workspace | lockfile-driven; CI installs with `--locked` and names the extras each job needs |
| API | **FastAPI** + uvicorn | async, typed, trivial to containerize |
| Vector DB | **Qdrant `v1.18.3`** (Docker) | cosine, 384-d. Exposes the Query API (`/points/query`, server ≥1.10). |
| Qdrant client | **`qdrant-client>=1.12,<2`** | Uses `query_points()` (present from client 1.10; the legacy `search()` was removed in 1.16). Client and server must both be on the Query API — see §12.1. |
| Embeddings | **`BAAI/bge-small-en-v1.5`** (384-d), via **`sentence-transformers>=5.4,<6`** | small, CPU-runnable. The floor is verified against the released wheels: `get_embedding_dimension()`, which `Embedder.dim` reads, exists from 5.4.0; earlier releases have only the old name, which 5.4 deprecated. |
| LLM | **Ollama `llama3.2:3b`** | self-hosted, open-source → cost/latency control, no per-token bill. Swappable via config. Decoding pinned by the `LLM` contract: temperature 0, seed 0. |
| Agent runtime (optional) | **`langgraph>=1.2,<2`**, `langchain-core>=1.6,<2` — the `agent` extra | used only by the `langgraph` engine and imported lazily, so the base install and the default `direct` engine never need it |
| Orchestration | **Prefect `>=3,<4`** | 2.x and 3.x APIs differ — pin the major |
| Tokenizer | **tiktoken `cl100k_base`** | one tokenizer for chunking AND the context budget so all token math agrees |
| Chunking | ~**512** tokens / ~**64** overlap | token-based |
| Retrieval | dense top-**20** candidates → lexical-overlap rerank → top-**k=5**, context budget **3000 tokens** | hybrid (BM25) and cross-encoder rerank still out of scope |
| Refusal | intent filter → evidence gate (score floor **0.45**, term overlap, high-confidence escape) → the model itself | an IDF-weighted coverage gate is built but shipped **disabled** (`0.0`) until a threshold is measured on the serving corpus |
| Rate limiting | Redis token bucket (ASGI middleware) | `/query` answers 503 if Redis is unreachable |
| Frontend | React + Vite + TypeScript | ask / answer / sources / quality |
| Tests | pytest (backend), Vitest (frontend) | |
| CI | GitHub Actions: `backend-ci`, `eval-quality` | lint, docs-check, audit-freshness and tests on every push; a fixture re-measurement with the delta posted on every PR |
| Deploy | Docker Compose (local) · Azure Container Apps (cloud) | GCP is a planned second target, not a replacement (§1) |

---

## 4. Repository structure

```
projects/rag-wikipedia/
├── docker-compose.yml           # redis · qdrant · ollama · api · frontend
├── Makefile                     # ingest · eval-fixture-all · audit-baseline · compare-engines
│                                #   · test · lint · docs-check
├── pyproject.toml / uv.lock     # uv workspace root; member: backend
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml           # dependencies; extras: dev, agent
│   ├── app/                     # the serving core: framework-free, cloud-free (enforced)
│   │   ├── main.py              # FastAPI app, rate-limit middleware, routers
│   │   ├── api/                 # query, health, quality, metrics
│   │   ├── core/                # config, chunking, embeddings, vectorstore, retrieval,
│   │   │                        #   refusal, idf, prompt, citations, llm, quality,
│   │   │                        #   runtime_config, rate_limit, metrics, holdout, fileio,
│   │   │                        #   providers (dormant registry, §17)
│   │   └── models/              # query.py (pydantic schemas)
│   ├── engines/                 # Engine contract + registry: direct, langgraph
│   ├── pipeline/                # sources.py, tasks.py, flow.py  (Prefect ingest)
│   ├── eval/                    # golden / holdout / adversarial suites, fixtures/, idf/,
│   │                            #   run_eval, audit, compare_engines, bench_ingest
│   └── tests/                   # pytest: unit, contract, conformance, layering
├── scripts/                     # docs_check, audit_freshness, compare_audit, build_idf,
│                                #   sweep_coverage, build_eval_fixture, build_serving_suite,
│                                #   verify_holdout
├── frontend/                    # React+Vite: QueryBox, AnswerView, CitationList, QualityPanel
├── demo/                        # console.html — single-file live demo UI
├── docs/                        # runbook, deployment-strategy, ingestion-performance,
│                                #   verifying-review-findings
└── infra/aca/                   # Azure Container Apps manifests
```

CI lives at the repository root in `.github/workflows/`: `backend-ci.yml` runs lint,
docs-check, audit-freshness and the tests on every push; `eval-quality.yml`
re-ingests the fixture corpus, re-runs the audit and posts the delta on every pull
request that touches retrieval, the eval harness or the pipeline.

---

## 5. Prerequisites

- **Docker Desktop** (WSL2 backend on Windows). Needs ≥ ~10 GB free RAM to run Qdrant + Ollama(3B) + the embedder together — see §12.5.
- **Python 3.12** + **uv** (`pip install uv`, ensure its Scripts dir is on PATH).
- **Node 20** (only to build the React frontend; the `demo/console.html` needs no build).
- Azure CLI (`az`) only for §11.

---

## 6. The files — a map

What each file owns, with a link to it in git. The code itself lives only in git — this section used to embed it verbatim, and those copies drifted (see the note at the top). Section numbers are unchanged, so references elsewhere in this document still resolve. The frontend in §6.23 is described rather than mapped.

### 6.1 `pyproject.toml`

**Files:** [`pyproject.toml`](projects/rag-wikipedia/pyproject.toml) — the uv workspace root (member: `backend`) and the shared ruff settings. [`backend/pyproject.toml`](projects/rag-wikipedia/backend/pyproject.toml) — the dependencies, each pin with its reason inline, and the `dev` and `agent` extras with the policy for adding more.

Install for development from `projects/rag-wikipedia/`, the workspace root: `uv sync --extra dev --extra agent --python 3.12`. CI installs with `--locked`, so a stale `uv.lock` fails the build instead of resolving a different dependency graph.

### 6.2 `backend/app/core/config.py`

**File:** [`backend/app/core/config.py`](projects/rag-wikipedia/backend/app/core/config.py) — every setting as a validated pydantic `Settings` field (env var = the upper-case name), ranges enforced at startup. §15 carries the generated list.

### 6.3 `backend/app/core/chunking.py`

**File:** [`backend/app/core/chunking.py`](projects/rag-wikipedia/backend/app/core/chunking.py) — token-based chunking with tiktoken `cl100k_base` (512 tokens, 64 overlap) and deterministic point IDs.

> **Key idea:** `point_id = sha256(source_id::chunk_index)[:32]` is **deterministic** → re-ingesting the same article upserts the *same* IDs → idempotent, no duplicates, resumable.

### 6.4 `backend/app/core/embeddings.py`

**File:** [`backend/app/core/embeddings.py`](projects/rag-wikipedia/backend/app/core/embeddings.py) — the `Embedder` interface, whose contract requires a probed `dim`, and `BGEEmbedder`.

> Behind an `Embedder` interface → swappable via config; the dormant registry (§17) holds Azure and OpenAI adapters. Every implementation must report a *probed* `dim` (§12.9).

### 6.5 `backend/app/core/vectorstore.py`

**File:** [`backend/app/core/vectorstore.py`](projects/rag-wikipedia/backend/app/core/vectorstore.py) — `QdrantStore`: collection creation with a dimension guard against the existing collection, batched upsert, `search` over the Query API, and `existing_ids` for resumable ingest.

> The public method is still called `search()` — that is **our** interface, and retrieval
> calls it. What changed underneath is the *client* call: `self._client.query_points(...)`
> instead of the removed `self._client.search(...)`. Client and server must both be on
> the Query API (client ≥1.10, server ≥1.10). See §12.1.
>
> `ensure_collection(dim)` takes the dimension from the embedder and refuses an existing
> collection created at a different size, naming both — the check that makes swapping
> embedders safe (§12.9).

### 6.6 `backend/app/core/retrieval.py`

**File:** [`backend/app/core/retrieval.py`](projects/rag-wikipedia/backend/app/core/retrieval.py) — `retrieve()`: intent filter, embed, dense top-`retrieval_candidate_k` search, lexical-overlap rerank, the evidence gate, then the token budget.

### 6.7 `backend/app/core/prompt.py`

**File:** [`backend/app/core/prompt.py`](projects/rag-wikipedia/backend/app/core/prompt.py) — the grounding contract every engine shares: context only, inline `[n]` citations on every sentence, and one exact refusal line.

### 6.8 `backend/app/core/citations.py`

**File:** [`backend/app/core/citations.py`](projects/rag-wikipedia/backend/app/core/citations.py) — extracts the `[n]` markers the model actually wrote, and builds citations only for indices that resolve to a retrieved chunk.

> Only indices the model **actually cited** become citations — no phantom sources.

### 6.9 `backend/app/core/llm.py`

**File:** [`backend/app/core/llm.py`](projects/rag-wikipedia/backend/app/core/llm.py) — the `LLM` interface, whose contract requires deterministic decoding, and `OllamaLLM`, which pins `num_ctx`, temperature 0 and seed 0 on every request.

### 6.10 `backend/app/models/query.py`

**File:** [`backend/app/models/query.py`](projects/rag-wikipedia/backend/app/models/query.py) — the API schemas: `QueryRequest` (1–500 characters), `Citation`, and `QueryResponse` with its explicit `refused` flag.

### 6.10b `backend/app/core/refusal.py`  *(the refusal contract)*

**File:** [`backend/app/core/refusal.py`](projects/rag-wikipedia/backend/app/core/refusal.py) — `REFUSAL_MESSAGE`, soft-refusal detection (`is_refusal`), the intent filter (`is_private_or_time_dependent`) and the evidence gate (`decide_evidence`).

> **Why this exists.** Refusal is a *product feature* here, so it must be an explicit
> part of the API contract, not something a UI guesses. Two refusal kinds are covered:
> a **hard refusal** (retrieval empty or below `refusal_threshold`, API returns
> `REFUSAL_MESSAGE`) and a **soft refusal** (the model itself declines despite having
> context). Both set `refused: true`.
>
> The same file owns the evidence gate, `decide_evidence`: the score floor, term overlap,
> the high-confidence escape, and the IDF-weighted coverage test that ships disabled (§3).

### 6.11 `backend/app/api/health.py`

**File:** [`backend/app/api/health.py`](projects/rag-wikipedia/backend/app/api/health.py) — `GET /health`.

> `/health` is **liveness only** — it never probes deps. Dependency failures surface as `503` from `/query`.

### 6.12 `backend/app/api/query.py`  *(the orchestration)*

**File:** [`backend/app/api/query.py`](projects/rag-wikipedia/backend/app/api/query.py) — `POST /query`: resolves the cached embedder, store and model, times every stage for `/metrics`, maps store and model failures to 503, and returns hard and soft refusals with `refused: true`. It does not dispatch through the engine registry yet (§2).

### 6.13 `backend/app/main.py`

**File:** [`backend/app/main.py`](projects/rag-wikipedia/backend/app/main.py) — the FastAPI app: rate-limit middleware, CORS, the four routers, and restoring a persisted runtime config at startup — logged, because those thresholds decide what the API refuses.

### 6.14 Pipeline — `backend/pipeline/sources.py`

**File:** [`backend/pipeline/sources.py`](projects/rag-wikipedia/backend/pipeline/sources.py) — the article stream for each profile, restarted on transient upstream failures.

> **Corpus:** `wikimedia/wikipedia`, config `20231101.en` (English Wikipedia, 1 Nov 2023 dump, maintained by the Wikimedia Foundation on Hugging Face; CC BY-SA 4.0). The `tiny` profile is the **first 500 articles** of the stream (a dev fixture, not a random sample — for a representative eval, `dataset.shuffle(seed=…)` before taking N). Three profiles: `fixture` (150 committed articles, no network — what CI and the audit use), `tiny` (500) and `real` (25,000).

### 6.15 Pipeline — `backend/pipeline/tasks.py`

**File:** [`backend/pipeline/tasks.py`](projects/rag-wikipedia/backend/pipeline/tasks.py) — the Prefect tasks — clean, chunk, embed, upsert — and `ingest_segment`, which embeds only the chunks not already stored.

> **`cache_policy=NO_CACHE` on the two side-effecting tasks is mandatory** — see §12.2.

### 6.16 Pipeline — `backend/pipeline/flow.py`

**File:** [`backend/pipeline/flow.py`](projects/rag-wikipedia/backend/pipeline/flow.py) — `ingest_flow`: batches articles into segments of 200, skips the held-out slice, sizes the collection from `embedder.dim`, and resumes from what is already stored. `--force` re-embeds in place.

### 6.17 Eval — `backend/eval/metrics.py`

**File:** [`backend/eval/metrics.py`](projects/rag-wikipedia/backend/eval/metrics.py) — recall@k, reciprocal rank and MRR, refusal accuracy, and lexical groundedness.

### 6.18 Eval — the suites: `golden.jsonl`, `holdout.jsonl`, `adversarial.jsonl`

**Files:** [`backend/eval/golden.jsonl`](projects/rag-wikipedia/backend/eval/golden.jsonl), [`backend/eval/holdout.jsonl`](projects/rag-wikipedia/backend/eval/holdout.jsonl), [`backend/eval/adversarial.jsonl`](projects/rag-wikipedia/backend/eval/adversarial.jsonl) — the committed suites the audit scores against the fixture corpus; their sizes and gates are in the generated table in §8. [`backend/eval/serving_golden.jsonl`](projects/rag-wikipedia/backend/eval/serving_golden.jsonl) describes the *served* corpus instead, and is built by `scripts/build_serving_suite.py`.

### 6.19 Eval — `backend/eval/run_eval.py`

Retrieval-only by default (no LLM → seconds): validates the suite up front, scores recall@k, precision@k and MRR on the answerable cases and the refusal metrics on the unanswerable ones, writes `report.json`, and **gates** unconditionally on all four thresholds (§8). `--with-groundedness` adds one LLM call per answerable case.

**File:** [`backend/eval/run_eval.py`](projects/rag-wikipedia/backend/eval/run_eval.py) — the scorer that the audit and `make eval-fixture` run.

> The up-front validation and unconditional gates, once described here as a "hardened variant", are simply the code now.

### 6.20 Infra — `docker-compose.yml`

**File:** [`docker-compose.yml`](projects/rag-wikipedia/docker-compose.yml) — the local stack. Redis is published on loopback only, because it has no password.

> Healthchecks use **only tools each image actually ships** — see §12.3. No `version:` key (obsolete in Compose v2).

### 6.21 Infra — `backend/Dockerfile`

**File:** [`backend/Dockerfile`](projects/rag-wikipedia/backend/Dockerfile) — the API image.

### 6.22 Infra — `Makefile`

**File:** [`Makefile`](projects/rag-wikipedia/Makefile) — every workflow as a target; §7 and §8 use them.

`eval` is retrieval-only and takes seconds. `eval-groundedness` adds one LLM
call per answerable case, so it needs Ollama and takes minutes — that is why it
is a separate target rather than a default. `eval-audit` scores golden, holdout
and adversarial together and writes `audit_report.json`, which `GET /quality`
serves. `docs-check` verifies this document's own factual claims.

The fixture targets come in pairs that pin profile and collection together — `eval-corpus`, `audit-baseline`, `eval-fixture`, `eval-fixture-all` — because scoring the committed suites against the served corpus reports confident numbers for the wrong corpus. `compare-engines` runs the head-to-head (§8); `idf` and `idf-fixture` rebuild the evidence tables after an ingest.

### 6.23 Frontend (React + Vite + TS) — spec

- `src/App.tsx`: state `{result, loading, error}`; `handleQuery(q)` → `POST ${API_BASE}/query`, where `const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')` (empty = same-origin nginx proxy locally; Azure bakes the URL at build). Renders `<QueryBox>`, `<AnswerView>`, `<CitationList>`. `Citation = {index, title, source_id, excerpt}`. The response also carries
**`refused: bool`** — the client must not infer a refusal from an empty
`citations[]`, because a correct grounded answer can omit its `[n]` markers. The trailing-slash strip is what stops a configured base such as `https://api.example/` from producing `//query`.
- `QueryBox`: form (`data-testid="query-form"`) with input (`query-input`) + submit (`query-submit`); calls `onSubmit(trimmed)`; both disabled while `loading`.
- `AnswerView`: renders the `answer` string.
- `CitationList`: renders each citation as an expandable item (`[index] title` → `excerpt`).
- `QualityPanel`: fetches `GET /quality` on mount and renders the per-suite metrics
  table; the **Run audit** button POSTs `/quality/audit`. It reads `metrics` (POST
  and GET return the same shape), slugifies the server-supplied `status` before
  using it as a class name, and derives the deployment's policy from the response
  — 403/404/405 disables the button and points at `make eval-audit`, 409 reports a
  concurrent audit.
- `nginx.conf.template` (prod): serve `dist/`; `location /query` and `/health`
  `proxy_pass ${API_UPSTREAM}`; `location = /quality` GET-only and public; and
  `location /quality/` returns **403** — running an audit is CPU-heavy and
  overwrites the reported quality state, so it is not a public operation.
- `frontend/Dockerfile`: node:20-alpine build (`ARG VITE_API_BASE_URL=""`) → nginx:alpine serving `dist/`.

### 6.24 `demo/console.html` — single-file live demo UI

A **no-build** ChatGPT-style page: a chat pane (question → grounded answer with expandable citations **or** controlled refusal, + live latency) and a metrics panel (loads `report.json` → recall@5 / MRR / refusal accuracy + GATE badge; system specs; live Qdrant vector count). Config: `const API_BASE = "http://localhost:8000"`. Serve it statically (`python -m http.server 8080 --directory demo`) → open `http://localhost:8080/console.html`. CORS is satisfied because `allowed_origins` defaults to `*`. **This is the demo you screen-share.**

**File:** [`demo/console.html`](projects/rag-wikipedia/demo/console.html) — the single-file demo.

### 6.25 The rest of `app/`

- [`api/quality.py`](projects/rag-wikipedia/backend/app/api/quality.py) — `GET /quality` (the last audit verdict, public) and `POST /quality/audit` (409 while one is running; `?auto_correct=true` requires `X-Quality-Token`).
- [`api/metrics.py`](projects/rag-wikipedia/backend/app/api/metrics.py) — `GET /metrics`: per-stage latency for this process.
- [`core/idf.py`](projects/rag-wikipedia/backend/app/core/idf.py) — the document-frequency table that weights evidence by how much each term narrows the corpus.
- [`core/quality.py`](projects/rag-wikipedia/backend/app/core/quality.py) — the quality-state singleton and audit provenance. It serves the committed report only when that report describes this deployment.
- [`core/runtime_config.py`](projects/rag-wikipedia/backend/app/core/runtime_config.py) — the five retrieval thresholds that can change at runtime: validated with the same bounds as `Settings`, persisted, and reversible.
- [`core/rate_limit.py`](projects/rag-wikipedia/backend/app/core/rate_limit.py) — the Redis token-bucket ASGI middleware. It fails closed to 503 when Redis is unreachable.
- [`core/metrics.py`](projects/rag-wikipedia/backend/app/core/metrics.py) — process-local latency sampling. Percentiles are withheld below a minimum sample count rather than guessed.
- [`core/holdout.py`](projects/rag-wikipedia/backend/app/core/holdout.py) — the held-out slice: 1 article in 100, never ingested, which keeps out-of-corpus eval cases valid as the corpus grows.
- [`core/fileio.py`](projects/rag-wikipedia/backend/app/core/fileio.py) — crash-safe writes.
- [`core/providers.py`](projects/rag-wikipedia/backend/app/core/providers.py) — the dormant LLM/embedder registry (§17), entirely commented out.

### 6.26 `backend/engines/` — the agent runtime

See §2 for the design.

- [`base.py`](projects/rag-wikipedia/backend/engines/base.py) — the `Engine` contract and `EngineResult`: orchestrate `app/core`, never reimplement it, and never translate errors.
- [`direct.py`](projects/rag-wikipedia/backend/engines/direct.py) — the v1 chain as an engine: the framework-free baseline.
- [`registry.py`](projects/rag-wikipedia/backend/engines/registry.py) — `ENGINE_CHOICE` → engine. The LangGraph engine is imported lazily, so the base install never needs the `agent` extra.
- [`langgraph_engine.py`](projects/rag-wikipedia/backend/engines/langgraph_engine.py) — triage → retrieve → grade → rewrite (bounded) → generate → verify.

### 6.27 The rest of `eval/`

- [`audit.py`](projects/rag-wikipedia/backend/eval/audit.py) — scores all three suites, writes `audit_report.json` with provenance, and appends to `audit_history.jsonl`; `--auto-correct` searches threshold candidates.
- [`compare_engines.py`](projects/rag-wikipedia/backend/eval/compare_engines.py) — the engine head-to-head (§8): one set of dependencies shared by every engine, a retry per case, `--resume`, and an INCOMPLETE banner when cases failed.
- [`bench_ingest.py`](projects/rag-wikipedia/backend/eval/bench_ingest.py) — the ingestion throughput benchmark. It creates its own collection, which is how it proves it owns what it later deletes.
- [`fixtures/`](projects/rag-wikipedia/backend/eval/fixtures) holds the committed 150-article corpus, and [`idf/`](projects/rag-wikipedia/backend/eval/idf) one IDF table per collection.

### 6.28 `scripts/`

- [`docs_check.py`](projects/rag-wikipedia/scripts/docs_check.py) — checks this documentation against the repository: regenerates the marked blocks and asserts the inline counts.
- [`audit_freshness.py`](projects/rag-wikipedia/scripts/audit_freshness.py) — fails when `audit_report.json` predates a change to a watched file.
- [`compare_audit.py`](projects/rag-wikipedia/scripts/compare_audit.py) — the delta table that `eval-quality` posts on a pull request.
- [`build_eval_fixture.py`](projects/rag-wikipedia/scripts/build_eval_fixture.py) — builds the committed evaluation corpus.
- [`build_idf.py`](projects/rag-wikipedia/scripts/build_idf.py) — builds a collection's IDF table from its indexed chunks.
- [`build_serving_suite.py`](projects/rag-wikipedia/scripts/build_serving_suite.py) — builds an evaluation suite for the serving corpus, derived from it.
- [`sweep_coverage.py`](projects/rag-wikipedia/scripts/sweep_coverage.py) — measures `refusal_min_evidence_coverage` and picks a threshold.
- [`verify_holdout.py`](projects/rag-wikipedia/scripts/verify_holdout.py) — verifies that no held-out article reached the index.

---

## 7. Run it locally

**Canonical path (Docker builds everything):**
```bash
make up                     # build + start every compose service (§2)
make pull-model             # one-time: pull llama3.2:3b (~2 GB, persists in a volume)
make ingest PROFILE=tiny    # ingest 500 articles → ~6.3k vectors
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question":"What is machine learning?"}'
make eval-fixture-all       # ingest the committed fixture, then score it (§8)
# UI: http://localhost:5173  (React)  or  the demo console (see §6.24)
```

**Resilient path (no Docker image build — the one used day to day, see §12.5):** run Redis, Qdrant and Ollama as containers and the API from the venv. Two details are not optional. Redis must be up, because `/query` answers 503 when the rate limiter cannot reach it. And its URL must say `127.0.0.1`: `localhost` can resolve to `::1` first — it does on the Windows machine this was built on — while compose publishes Redis on IPv4 only, which produces the same 503 from a perfectly healthy Redis.
```bash
docker compose up -d redis qdrant ollama
RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000   # from backend/
PROFILE=tiny uv run python backend/pipeline/flow.py             # ingest
python -m http.server 8080 --directory demo                     # serve the console
```

On a 16 GB machine, stop the API before `make audit-baseline`, `make eval-corpus` or `make compare-engines`: each loads its own copy of the embedding model, and a second copy alongside Ollama is what runs out of memory (§12.5).

---

## 8. Evaluation & gates

Retrieval quality is measured against a **fixed, committed corpus** — 150 articles,
gzipped in `backend/eval/fixtures/`, ingested into its own collection
`wikipedia_eval` — so a local result and a CI result describe the same thing. The
working corpus (`wikipedia`, from the `real` profile) is what `/query` serves; it is
never scored against the committed suites, and the paired `make` targets pin profile
and collection together so that cannot happen by accident.

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

Per suite: `recall@k`, `precision@k` and `MRR` on the answerable cases;
`refusal_accuracy`, `false_accept_rate` and `answerable_refusal_rate` on refusals.
`false_accept_rate` and `answerable_refusal_rate` are always read together — any
system can drive the first to zero by refusing everything.

- **`make audit-baseline`** writes `audit_report.json` with provenance (git sha,
  profile, collection, vector count). **`make audit-freshness`** fails in CI when a
  watched file has changed since that commit. Regenerate the report; never hand-edit it.
- **The absolute gates are red on purpose.** `false_accept_rate` was 0.600 on holdout
  against a 0.10 gate as of 2026-09-11, and no threshold has been relaxed. The
  `eval-quality` workflow therefore gates pull requests on **regression against the
  committed baseline** instead, and posts the delta table on the PR.
- **Retrieval-only vs end-to-end.** The audit scores retrieval: `refused` comes from
  `retrieve()`, with no model involved. `make compare-engines` scores whole engines
  end-to-end, so the model's own refusals count too. They measure different things and
  must never be quoted as one number. Measured, with the per-case reconstruction:
  [docs/retrieval-only-vs-end-to-end.md](projects/rag-wikipedia/docs/retrieval-only-vs-end-to-end.md).
- **Engine head-to-head** (`backend/eval/engine_comparison.md`, 2026-09-10, holdout +
  adversarial, 40 cases, zero errors). `langgraph` matched `direct` exactly on
  `false_accept_rate` (0.000 and 0.100) and on cost (0.90 and 0.75 LLM calls per
  case), and was **worse** on `answerable_refusal_rate` (0.067 → 0.133 and
  0.000 → 0.200): its citation-verify step refused three answerable questions that
  `direct` answered correctly. Its rewrite branch executed **zero** times, because the
  evidence gate never refused a non-private question on this corpus, so the retry loop
  never had anything to do. Recorded as found; see §12.12.

---

## 9. Testing

`pytest` — **331 tests**: chunking (deterministic IDs), config, generation (prompt/citation), metrics, pipeline idempotency and resumability, retrieval (empty + below-threshold refusal), the evidence gate and IDF coverage, rate limiting, the quality endpoint, API (validation, 503 mapping, refusal), and **contract tests** for `QdrantStore` against an **in-memory Qdrant** (`QdrantClient(":memory:")`) — because mocking the store is what let a client API break reach prod (§12.1). Added in September 2026:

- **layering** — `app/` imports nothing outside an allowlist of base packages; the `direct` engine stays framework-free; the engine registry never imports a framework at module level.
- **engine conformance** — a *differential* test running `POST /query` and `DirectEngine` over identical mocks and asserting identical answers, citations and refusals on every branch of the handler.
- **interface contracts** — deterministic decoding for `LLM`; a probed `dim` for `Embedder`, including a library that has dropped the deprecated method name and a model that reports no fixed dimension.
- **eval-harness resilience** — a failed model call is retried once, then recorded as an error and excluded, never scored as a refusal.

Vitest covers the four frontend components. Run `make test`, `make lint` and `make docs-check` before declaring any milestone done.

---

## 10. Frontend (React) — build & run

- Dev: `cd frontend && npm install && npm run dev` (Vite dev server, proxies `/query` → `http://localhost:8000`).
- Prod (Compose): the `frontend` container builds `dist/` (node:20-alpine) and serves it via nginx, which proxies `/query` and `/health` to `api:8000`. Open `http://localhost:5173`.
- For a **no-build** UI (recommended for demos), use `demo/console.html` (§6.24) instead — it needs only a static file server and the running API.

## 11. Azure deployment (Container Apps)

Deploy all 4 services to **Azure Container Apps**. Condensed happy path (full manifests in `infra/aca/`):

1. `az login`; ensure the subscription is enabled and the resource group exists.
2. **Register providers:** `Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.ContainerRegistry`, `Microsoft.Storage` (subscription-owner action).
3. **ACR** (`az acr create`) → build images server-side (`az acr build` — no local Docker).
4. **Log Analytics** + **Container Apps environment** with workload profiles; add a **D4 dedicated profile** for Ollama (needs ~8 GB).
5. **Storage account** + 2 Azure Files shares (`qdrant-data`, `ollama-models`); link them to the environment.
6. Deploy **qdrant** + **ollama** (internal ingress, persistent volumes) via an **ARM template** (`az deployment group create`) — the CLI `--yaml` path can 400 on preview extensions; ARM `2024-03-01` is stable.
7. Pull the model into the ollama app (temporarily flip its ingress external, `POST /api/pull`, flip back).
8. Deploy **rag-api** (external, ACR image; pull creds via managed identity *or* ACR admin fallback if you lack RBAC-admin). **Internal service URLs must include the port:** `http://qdrant:80`… *(verify — see §12.4)*.
9. Build the **frontend** in ACR with `VITE_API_BASE_URL=https://<api-fqdn>`, deploy, then lock `ALLOWED_ORIGINS` to the frontend origin.
10. Ingest into the deployed Qdrant, then smoke-test `/query`.

**Cost note:** the **D4 node is the cost driver** (~$0.30+/hr while up). On a Visual Studio / student subscription, hitting the spending cap auto-disables the subscription (safe: no real charge, but everything suspends). Tear down when done: `az containerapp delete …` for each app, then env, ACR, storage, Log Analytics.

### 11.1 `infra/aca/backing-apps.json` — ARM template for qdrant + ollama (step 6)

Deploy with `az deployment group create -g <RG> -n backing-apps -f infra/aca/backing-apps.json -p environmentId="<ENV_ID>"`. Fully parameterized (no hardcoded subscription). Qdrant image is `v1.18.3`, matching the client pin and the `query_points` code path (§12.1). **Never deploy it over a volume written by v1.9.2** — Qdrant does not support skipping minor versions and the storage migration is irreversible; reindex into a fresh volume, or upgrade in stages with snapshots.

**File:** [`infra/aca/backing-apps.json`](projects/rag-wikipedia/infra/aca/backing-apps.json) — the ARM template for the two stateful backing apps.

The `rag-api` and `rag-frontend` apps are created with `az containerapp create` (external ingress, ACR image, env vars `QDRANT_URL`/`OLLAMA_URL` pointing at the internal FQDNs, `ALLOWED_ORIGINS` locked to the frontend origin) — see §11 steps 8–9.

---

## 12. Hard-won lessons (read this first when something breaks)

**12.1 — `/query` returns 503 `'QdrantClient' object has no attribute 'search'`.**
An unbounded `qdrant-client>=1.9` resolves to a modern client that **removed `.search()`** (gone in 1.16) and uses the Query API (`/points/query`, server ≥1.10). Paired with a 1.9.2 server that produces `503 Vector store unavailable` and `'QdrantClient' object has no attribute 'query_points'`. Two valid fixes existed — **pick one, never mix:** (a) pin `qdrant-client<1.10` and keep `.search()` + server 1.9.2; or (b) run server `v1.18.3` **and** call `query_points(...).points`.

**This repo chose (b).** Current state: server `v1.18.3`, client `>=1.12,<2`, `vectorstore.py` calls `query_points`. In-memory tests pass under either option, which is exactly why `tests/test_vectorstore.py` exists — it exercises the real client method so a client-API regression fails before release.

**Two traps this cost us in practice:**
1. **A merge silently reverted the pin.** Merging a branch that carried option (a)'s `<1.10` pin on top of option (b)'s code reintroduced the 503. The lesson: the pin and the call site are *one decision* — review them together.
2. **The volume is not portable across the jump.** Starting v1.18.3 on storage written by v1.9.2 panics at boot with ``Failed to deserialize .../segment.json: unknown variant `on_disk` `` and the container exits **101**. There is no in-place fix: drop the volume and re-ingest, or stage the upgrade through every intermediate minor with snapshots.

**12.2 — Ingestion dies `HashError: cannot pickle '_thread.RLock'`.**
Prefect hashes task inputs for its result cache; `embed_chunks`/`upsert_to_qdrant` receive an embedder/`QdrantStore` holding unpicklable locks. Fix: `@task(cache_policy=NO_CACHE)` on those two side-effecting tasks (caching was meaningless for them anyway).

**12.3 — Containers stuck "unhealthy" / `dependency failed to start`.**
The healthchecks called `wget`/`curl`, which **none of the images ship** (`qdrant`, `ollama`, `python:slim`). Use tools each image has: bash `/dev/tcp` for Qdrant, `ollama list` for Ollama, `python -c "urllib…"` for the API.

**12.4 — On Azure, `/query` times out reaching internal services.**
ACA internal ingress listens on 80/443, not the app's target port, and `qdrant-client` defaults to `:6333` when the URL omits a port. Set explicit ports on internal URLs and verify the exact form against current ACA behavior before trusting it.

**12.5 — Docker Desktop keeps crashing (`error reading from server: EOF`, missing named pipe).**
On a 16 GB machine, an **image build** (torch/transformers download) or running **Ollama's 3B model + the embedder + the Docker VM** together **OOMs and kills the daemon**. Mitigations: (a) `ENV UV_TORCH_BACKEND=cpu` to shrink the image; (b) **avoid builds** — run Qdrant+Ollama as containers and the API from the venv (§7 resilient path); (c) close RAM hogs (a browser can eat 5 GB); (d) recover the daemon with `wsl --shutdown` then relaunch Docker Desktop; (e) if memory is tight, use `llama3.2:1b` (`LLM_MODEL` config) — the model is swappable by design. *(2026-09-11: RAM, not disk, is now the binding constraint. With the stack and a browser up, free RAM measured 1.7 GB of 15.7, and a second embedding-model load in another process failed outright. Stop the native API before audits and evals — that freed 3.8 GB — and restart it afterwards.)*

**12.6 — First `/query` after startup is slow or drops the LLM connection.**
The 3B model loads into RAM on first inference. Warm it once (`POST /api/generate` with a tiny prompt) before demoing; keep `QdrantClient(timeout=60)` so bulk upserts don't time out.

**12.7 — A grounded question returns a refusal.**
Usually correct: with the `tiny` profile (first 500 articles) that topic isn't in the corpus, so retrieval is empty/weak and the refusal path fires. Verify with a topic you know is covered, or ingest more. It's the reliability logic working, not a bug.

**12.8 — The same question returns one citation, then two, with nothing changed.**
`OllamaLLM` sent only `num_ctx`, so the served path inherited Ollama's default temperature of **0.8** and sampled every answer. Fix: deterministic decoding is now a contract on the `LLM` interface (temperature 0, seed 0), not a setting on one adapter. Verified: the same question returns a byte-identical answer across process restarts, Docker restarts and days. Greedy decoding removes sampling variance, not hardware or server-build variance.

**12.9 — A hardcoded dimension rejects every embedder but one.**
`vectorstore.py` had `EXPECTED_DIM = 384`, and a test asserted that `dim=768` was an error — 768 being Vertex `text-embedding-005`. `Embedder.dim` is now *probed* from the loaded model, and `ensure_collection` compares it with the existing collection, which the old check never did. Then the library renamed the method it relies on (sentence-transformers 5.4.0: `get_sentence_embedding_dimension` → `get_embedding_dimension`, the old name left as a `FutureWarning` alias). Set floors by reading the released wheels, not the changelog.

**12.10 — A workflow that has never run is a claim, not a check.**
`eval-quality.yml` existed only on a feature branch and was triggered only by pull requests, so it had never executed while the README said a quality delta was posted on every PR. `workflow_dispatch` returns **404** for a workflow absent from the default branch. It first ran on PR #6. Check `gh workflow list` before trusting a workflow you have not watched run.

**12.11 — Never squash or rebase-merge.**
`audit_report.json` records the commit that produced it, and `audit-freshness` runs `git cat-file -e` on that SHA. Squash and rebase rewrite every SHA, so the recorded commit stops existing on `main` and the gate fails on the first push after the merge. Merge commits only.

**12.12 — A retry loop that never runs proves nothing.**
The LangGraph engine's rewrite branch executed zero times in 40 cases, because the evidence gate it depends on never refused a non-private question. Count which branches a graph actually took, not only what it returned: a null result from an unreachable branch is a finding about the gate, not a verdict on the pattern.

---

## 13. Acceptance checklist

- [ ] `docker compose up` → every compose service healthy on a clean machine (§2 lists them).
- [ ] `make pull-model` then `make ingest PROFILE=tiny` → Qdrant populated (~6.3k vectors); a re-run adds **no duplicate points** (deterministic IDs overwrite in place). Note this is *idempotent, not cheap*: every article is still streamed, cleaned, chunked and re-embedded, so a re-run costs the same as the first on the `real` profile. An article-level checkpoint would be needed to make it actually skip work.
- [ ] `POST /query` → grounded answer **with citations**; declines when no relevant context.
- [ ] LLM/embedder **model** swappable via config within the local providers (`LLM_MODEL=llama3.2:1b`, `EMBED_MODEL=...`). *Cross-provider* swapping (Azure/OpenAI via `LLM_CHOICE`) is **future work** — the registry is dormant (§17).
- [ ] `make eval-fixture-all`, then `make audit-baseline` → per-suite metrics with provenance, and `make audit-freshness` green. The absolute gates are known-red (§8); a **regression** is what fails.
- [ ] `make test`, `make lint`, `make docs-check` green — including the Qdrant contract tests, the engine conformance test and the layering test.
- [ ] `make compare-engines` produces a report with no `INCOMPLETE` banner.
- [ ] On the PR, both workflows green: `backend-ci`, and `eval-quality` with its delta comment posted.
- [ ] Demo console answers a grounded question and a refusal, shows latency + metrics.

---

## 14. Positioning (for clients / interviews)

This project demonstrably covers the **whole chain** — ingestion → chunking → retrieval → grounded generation → **evaluation** → API → **deployment** — with production concerns Inès/Baris-type clients name explicitly: **robustness, cost, latency, reliability**. Differentiators to say out loud: the **refusal path** (measured by refusal accuracy), **hand-rolled RAG** (you understand chunking / token budget / the retrieval→prompt contract, not just gluing a framework — and the `Embedder`/`LLM` interfaces make an Azure OpenAI adapter a drop-in, with the registry written and ready to activate, §17), a measured **performance analysis** — and, more tellingly, its own correction: the 25k profile was predicted at ~404k vectors from an N=150 sample and actually produced **87,173** (4.6x over), because Wikipedia dumps front-load their long articles; the GPU plan predicted 20-90 min and delivered far less because the card thermally clamps to 210 MHz of 2100, and the bottleneck moved from embedding to per-article overhead until batching removed it, and honest **"operational vs in-progress"** framing. Since September it also shows the harder half of the job: the model, the embedder and the agent runtime are each **one value**, behind interfaces whose contracts are enforced by tests — and a framework was **measured before it was trusted**. The LangGraph engine was built, scored against the framework-free baseline on the same suites, and did not earn its place on this corpus (§8); the reason was traced to a branch that never ran, rather than left as a verdict on the framework. Next levers: make the rewrite path reachable by measuring and enabling the IDF coverage gate; a Vertex AI adapter behind the dormant registry (§17), with a 768-d re-ingest into a new collection; hybrid search (BM25 + dense); LLM-judge groundedness.

---

## 15. Environment variables

| Var | Default | Meaning |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | embedder (swappable) |
| `LLM_MODEL` | `llama3.2:3b` | LLM (swap to `:1b` for low RAM) |
| `COLLECTION` | `wikipedia` | Qdrant collection |
| `TOP_K` | `5` | retrieval depth |
| `PROFILE` | `tiny` | `tiny` (500) or `real` (25k) |
| `TOKEN_BUDGET` | `3000` | context budget (tiktoken) |
| `REFUSAL_THRESHOLD` | `0.3` | top-1 cosine below → refuse |
| `ALLOWED_ORIGINS` | `*` | CORS (lock to the frontend origin in prod) |
| `ENGINE_CHOICE` | `direct` | engine registry: `direct` \| `langgraph`. Read from the environment, not `Settings` (§2) |

The complete list, generated from `Settings` by `make docs-write` and checked in CI:

<!-- docs-check:begin settings -->
`Settings` exposes **24 settings** (env var = the upper-case name); see `backend/.env.example`.

```text
QDRANT_URL
OLLAMA_URL
EMBED_MODEL
LLM_MODEL
COLLECTION
LLM_NUM_CTX
TOP_K
PROFILE
TOKEN_BUDGET
REFUSAL_THRESHOLD
REFUSAL_MIN_SCORE
REFUSAL_HIGH_CONFIDENCE_SCORE
REFUSAL_MIN_MARGIN
REFUSAL_MIN_OVERLAP_TERMS
REFUSAL_MIN_EVIDENCE_COVERAGE
RETRIEVAL_CANDIDATE_K
ALLOWED_ORIGINS
RATE_LIMIT_ENABLED
RATE_LIMIT_REDIS_URL
RATE_LIMIT_QUERY_PER_MINUTE
RATE_LIMIT_QUERY_BURST
RATE_LIMIT_CLIENT_HEADER
RATE_LIMIT_REDIS_TIMEOUT_SECONDS
QUALITY_ADMIN_TOKEN
```
<!-- docs-check:end -->

---

## 16. git — the source of truth

The code lives in git; this document describes it rather than copying it (§6). Keep it on GitHub:
- Mainline lives on `main`. Merge with a **merge commit, never squash or rebase** — `audit_report.json` records the commit that produced it, and rewriting SHAs breaks `audit-freshness` (§12.11).
- Current working branch: `feat/agnostic-engines` (pushed): the engine registry, the LangGraph engine and the comparison harness.
- To rebuild: `git clone <repo>` → §7. For disaster recovery, keep a second remote or a mirror — this document on its own is no longer a copy of the code.

## 17. Switching the LLM/embedder — the provider registry (dormant capability)

> ⚠️ **Status: NOT ACTIVE — this is future work, not shipped behavior.**
> `backend/app/core/providers.py` is **100% commented out** (0 non-comment lines; it
> imports to an empty module), and `app/api/query.py` still constructs `BGEEmbedder`
> and `OllamaLLM` directly. **`LLM_CHOICE` / `EMBED_CHOICE` do nothing today.** The
> registry becomes real only after the activation steps below are done *and tested*.
> Do not describe provider swapping as a working feature until then.

The whole app depends only on the `Embedder`/`LLM` interfaces (§6.4, §6.9), so the model is a *configuration* choice rather than a code change. The same name → factory pattern now backs the agent runtime too (`backend/engines/registry.py`, `ENGINE_CHOICE`, §2) — and that registry is in use by the eval harness. `providers.py` holds a **dormant** (fully commented) **one-value registry** that is designed to make this real: once activated, the same single change swaps **Ollama-3B → 1B**, or **Ollama → Azure OpenAI / OpenAI / any OpenAI-compatible server**. Kept dormant so it cannot affect the running app.

**Why it's written now:** on day one at a client that uses Azure OpenAI or OpenAI (or a self-hosted vLLM/Mistral endpoint), the design work is already done — activate it and point the same RAG at their stack instead of rewriting.

**The pattern** — a name → factory registry, selected by one env var:

```python
LLM_REGISTRY = {
    "ollama-3b": lambda: OllamaLLM("llama3.2:3b", OLLAMA_URL),
    "ollama-1b": lambda: OllamaLLM("llama3.2:1b", OLLAMA_URL),
    "azure":     lambda: AzureOpenAILLM(AZURE_CHAT_DEPLOYMENT, AZURE_ENDPOINT, AZURE_KEY),
    "openai":    lambda: OpenAILLM("gpt-4o-mini", OPENAI_KEY, OPENAI_BASE_URL),  # base_url → any OpenAI-compatible server
    # add ANY new LLM (open-source or not) as ONE line
}
def make_llm(): return LLM_REGISTRY[os.getenv("LLM_CHOICE", "ollama-3b")]()
```
(`EMBED_REGISTRY` / `make_embedder()` mirror this with `bge` | `azure` | `openai`.)

**Swapping is then identical for everything:** `LLM_CHOICE=ollama-1b`, `LLM_CHOICE=azure`, `LLM_CHOICE=openai`. One value.

**Activate it (e.g. to run on a client's LLM):**
1. Uncomment `providers.py`.
2. Add `"openai>=1.30"` to `backend/pyproject.toml`; `uv sync --all-extras`.
3. In `app/api/query.py`, point the two factories at the registry:
   ```python
   from app.core.providers import make_embedder, make_llm
   @lru_cache(maxsize=1)
   def _embedder(): return make_embedder()
   @lru_cache(maxsize=1)
   def _llm():      return make_llm()
   ```
4. Set `LLM_CHOICE`/`EMBED_CHOICE` + the chosen provider's credentials (`OPENAI_API_KEY`, or the `AZURE_OPENAI_*` set).
5. **Re-ingest into a NEW collection** if you switched the *embedder*: dimensions differ (bge-small 384, Vertex `text-embedding-005` 768, `text-embedding-3-small` 1536, `-3-large` 3072), and vectors of different dimensions are not interchangeable. There is nothing to configure: `Embedder.dim` is probed from the model, and `ensure_collection` refuses a collection created at a different size, naming both. The dormant adapters already carry `dim` and the `batch_size` keyword the pipeline passes. *(This step used to prescribe an `EMBED_DIM` variable and an env-driven `EXPECTED_DIM`; both were superseded on 2026-09-08.)*

Switching only the **LLM** (keeping `bge` embeddings) needs no re-ingest.

*Last captured 2026-09-11 from branch `feat/agnostic-engines`: Qdrant server **v1.18.3** + `qdrant-client>=1.12,<2` + `query_points()`; `sentence-transformers>=5.4,<6`; the explicit **`refused`** API contract; the engine registry (`direct`, `langgraph`); the `LLM`, `Embedder` and layering contracts. Verified end-to-end locally — grounded answers with citations, controlled refusals, byte-identical answers across restarts — and green in CI.*
