# Test Coverage Summary

This document answers the current test-coverage question for the RAG Wikipedia
project. It summarizes what is tested, how the tests are split between unit and
integration-style coverage, and what coverage gaps remain.

## Current Snapshot

The project currently has:

| Area | Current coverage |
|---|---:|
| Backend pytest tests | 69 tests |
| Backend test files | 13 files |
| Frontend Vitest tests | 7 tests |
| Frontend test files | 3 files |
| Numeric line/branch coverage | Not configured |

The backend suite was verified with:

```bash
cd projects/rag-wikipedia/backend
uv run pytest -q
uv run ruff check app/ pipeline/ eval/ tests/
```

Latest local result:

```text
69 passed, 1 warning
All ruff checks passed
```

The frontend test command exists, but local execution currently fails because
`vitest` is not installed in `node_modules`. This is a dependency reproducibility
gap, not a failed frontend assertion.

## Unit Test Coverage

The strongest unit coverage is around deterministic RAG behavior:

| Test area | Files | What it proves |
|---|---|---|
| Chunking and cleaning | `test_chunking.py` | Text cleaning, chunk creation, overlap behavior, and deterministic point IDs |
| Configuration | `test_config.py` | Default settings and environment-variable overrides |
| Prompt and citations | `test_generation.py` | Grounded prompt construction, citation extraction, and citation mapping |
| Metrics | `test_metrics.py` | `recall@k`, reciprocal rank, MRR, refusal accuracy, and groundedness scoring |
| Refusal classifier | `test_refusal.py` | Exact refusals, soft refusals, quote normalization, and grounded answers without citation markers |

These tests do not require Qdrant, Ollama, Docker, network access, or a live model.
They are fast and deterministic.

## Mocked Wiring Tests

Some tests verify application flow while replacing slow or external dependencies
with mocks:

| Test area | Files | What it proves |
|---|---|---|
| Query API behavior | `test_api.py`, `test_refusal.py` | `/query` handles validation, grounded answers, hard refusals, soft refusals, and dependency failures |
| Retrieval behavior | `test_retrieval.py` | Token-budget truncation, empty retrieval refusal, and low-score refusal |
| Ingestion pipeline | `test_pipeline.py` | Re-ingesting the same articles preserves vector count through deterministic IDs |
| Eval harness | `test_run_eval.py` | Groundedness is opt-in, the fast path avoids LLM calls, and gates do not depend on groundedness |
| Benchmark safety | `test_bench_ingest.py` | Benchmark collections cannot accidentally overwrite the primary collection |

This layer gives integration-like confidence in the code wiring without requiring
live Qdrant or Ollama services.

## Integration and Contract Test Coverage

The project has integration-lite and contract coverage, but not a full live service
integration suite.

Current integration-style coverage:

| Type | Files | What it covers |
|---|---|---|
| FastAPI in-process tests | `test_api.py`, `test_health.py`, API cases in `test_refusal.py` | Routes are exercised through `TestClient` without opening a network port |
| Qdrant contract tests | `test_vectorstore.py` | Uses real in-memory Qdrant client behavior for collection creation, upsert, search, count, and dimension validation |

The Qdrant contract tests are important because a previous production-like failure
came from mocking the vector store while the real Qdrant client API had changed.
The test now exercises the real client API surface in memory, so that class of
breakage is caught earlier.

## Evaluation Coverage

The project also has RAG-quality evaluation coverage, separate from ordinary unit
tests:

| Eval area | Files | What it protects |
|---|---|---|
| Retrieval quality metrics | `test_metrics.py`, `test_run_eval.py` | `recall@k` and MRR behavior |
| Refusal quality | `test_metrics.py`, `test_run_eval.py` | Refusal accuracy and required unanswerable cases |
| Groundedness metric | `test_metrics.py`, `test_run_eval.py` | Groundedness can be reported when enabled but does not gate the fast eval path |

Operational commands:

```bash
cd projects/rag-wikipedia
make eval
make eval-groundedness
```

`make eval` is intentionally retrieval-only and fast. `make eval-groundedness`
opts into LLM calls because groundedness requires generated answers.

## Frontend Test Coverage

Frontend tests are component-level tests:

| File | Test count | What it covers |
|---|---:|---|
| `AnswerView.test.tsx` | 1 | Answer text rendering |
| `CitationList.test.tsx` | 3 | Empty citation state, citation titles, and expandable citation detail |
| `QueryBox.test.tsx` | 3 | Input rendering, trimmed submit behavior, and disabled loading state |

These tests cover component behavior, not browser-level end-to-end behavior.

## Current Gaps

The main gaps are:

- No numeric line or branch coverage report is configured for backend or frontend.
- No full live integration test starts FastAPI, Qdrant, Ollama, and the frontend
  proxy together.
- No deployment smoke test verifies a deployed revision's health, query, refusal,
  and frontend behavior.
- Frontend test reproducibility needs a committed package lockfile and dependency
  install path so `npm test` works reliably in CI.
- Backend has strong behavioral coverage, but no static type-checking gate yet.

## Mentor-Facing Summary

The project has solid behavioral test coverage for the core RAG risks: chunking,
retrieval, citations, refusals, eval gates, and Qdrant client compatibility. Most
backend tests are unit or mocked wiring tests, with targeted integration-lite
coverage through FastAPI `TestClient` and in-memory Qdrant contract tests.

The project does not currently claim numeric test coverage. The next maturity step
would be to add coverage tooling, frontend dependency reproducibility, full-service
integration tests, and deployment smoke tests.
