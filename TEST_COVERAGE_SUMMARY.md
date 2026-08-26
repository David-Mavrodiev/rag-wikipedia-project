# Test Coverage Summary

This document answers the current test-coverage question for the RAG Wikipedia
project. It summarizes what is tested, how the tests are split between unit and
integration-style coverage, and what coverage gaps remain.

## Current Snapshot

Numeric line/branch coverage is **not configured**; the counts below are a test
inventory, not a percentage claim.

Reproduce with:

```bash
cd projects/rag-wikipedia/backend
uv run pytest -q
uv run ruff check app/ pipeline/ eval/ tests/

cd ../frontend
npm ci && npm run test -- --run
npx tsc --noEmit
```

Backend lint and tests run in CI on every push and pull request, on Python 3.12;
the frontend suite is not wired into CI yet.

The inventory below is **generated** by `scripts/docs_check.py` from
`pytest --collect-only`, not maintained by hand — this document previously
claimed 69 tests while the suite had grown well past it. `make docs-check`
fails if it drifts.

## Inventory

<!-- docs-check:begin test-inventory -->
Backend: **199 tests** across 18 files.

```text
test_refusal.py          56
test_run_eval.py         21
test_quality.py          16
test_runtime_config.py   16
test_metrics.py          15
test_config.py           14
test_llm.py              7
test_api.py              6
test_bench_ingest.py     6
test_generation.py       6
test_retrieval.py        6
test_audit.py            5
test_chunking.py         5
test_pipeline.py         5
test_startup_config.py   5
test_vectorstore.py      5
test_rate_limit.py       4
test_health.py           1
```

Frontend: **11 tests** across 4 files.

```text
AnswerView.test.tsx      1
CitationList.test.tsx    3
QualityPanel.test.tsx    4
QueryBox.test.tsx        3
```
<!-- docs-check:end -->

## Unit Test Coverage

The strongest unit coverage is around deterministic RAG behavior:

| Test area | Files | What it proves |
|---|---|---|
| Chunking and cleaning | `test_chunking.py` | Text cleaning, chunk creation, overlap behavior, and deterministic point IDs |
| Configuration | `test_config.py` | Defaults, environment overrides, and that out-of-range values are rejected at startup instead of silently changing retrieval |
| Prompt and citations | `test_generation.py` | Grounded prompt construction, citation extraction, and citation mapping |
| Metrics | `test_metrics.py` | `recall@k`, reciprocal rank, MRR, refusal accuracy, and groundedness scoring |
| Refusal classifier | `test_refusal.py` | Exact and soft refusals, quote normalization, grounded answers without citation markers, whole-token evidence overlap, and that answerable questions are never short-circuited as private or time-dependent |
| Runtime retrieval config | `test_runtime_config.py` | Bounds validation, atomic persistence, an absolute config path, and rejection of malformed or out-of-range files |
| LLM client | `test_llm.py` | The Ollama client sends its own `num_ctx`, so generation does not depend on how the server happened to be started |

These tests do not require Qdrant, Ollama, Redis, Docker, network access, or a live
model. They are fast and deterministic.

## Mocked Wiring Tests

Some tests verify application flow while replacing slow or external dependencies
with mocks:

| Test area | Files | What it proves |
|---|---|---|
| Query API behavior | `test_api.py`, `test_refusal.py` | `/query` handles validation, grounded answers, hard refusals, soft refusals, and dependency failures |
| Retrieval behavior | `test_retrieval.py` | Token-budget truncation, empty retrieval refusal, low-score refusal, and the high-score/no-overlap refusal branch |
| Rate limiting | `test_rate_limit.py` | 429 after the limit, rejection before expensive query work, `/health` exempt, and a fail-closed 503 when Redis is unreachable |
| Quality endpoint | `test_quality.py` | The audit runs off the event loop, a concurrent audit gets 409, auto-correction is token-gated, an unreadable report degrades instead of 500ing, and POST returns the same shape as GET |
| Startup restore | `test_startup_config.py` | A persisted retrieval config is re-applied and logged at boot; missing, truncated or out-of-range files fall back to defaults |
| Audit gates | `test_audit.py` | The golden/holdout recall-gap gate, and that a failed auto-correction restores the original config and returns no reports |
| Ingestion pipeline | `test_pipeline.py` | Re-ingesting preserves vector count through deterministic IDs, **and is resumable**: a second run skips embedding entirely, a partially ingested article re-embeds only its missing chunks, and `--force` re-embeds everything |
| Eval harness | `test_run_eval.py` | Groundedness is opt-in, the fast path avoids LLM calls, gates do not depend on groundedness, and precision and recall agree on title normalization |
| Benchmark safety | `test_bench_ingest.py` | Benchmark collections cannot overwrite the primary collection, and ownership is established by creating the collection rather than by checking first |

This layer gives integration-like confidence in the code wiring without requiring
live Qdrant or Ollama services.

## Integration and Contract Test Coverage

The project has integration-lite and contract coverage, but not a full live service
integration suite.

Current integration-style coverage:

| Type | Files | What it covers |
|---|---|---|
| FastAPI in-process tests | `test_api.py`, `test_health.py`, `test_quality.py`, plus API cases in `test_refusal.py` and `test_rate_limit.py` | Routes are exercised through `TestClient` without opening a network port |
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
| Retrieval quality metrics | `test_metrics.py`, `test_run_eval.py` | `recall@k`, `precision@k` and MRR behavior |
| Refusal quality | `test_metrics.py`, `test_run_eval.py`, `test_refusal.py` | Refusal accuracy, required unanswerable cases, and both directions of the refusal decision |
| Groundedness metric | `test_metrics.py`, `test_run_eval.py` | Groundedness can be reported when enabled but never gates the fast eval path |
| Overfit auditing | `test_audit.py` | The golden/holdout recall gap is caught before the suite is reported as healthy |

Operational commands:

```bash
cd projects/rag-wikipedia
make eval                 # retrieval-only, seconds
make eval-groundedness    # opts into one LLM call per answerable case
make eval-audit           # scores golden, holdout and adversarial together
```

`make eval` is intentionally retrieval-only and fast. `make eval-groundedness`
opts into LLM calls because groundedness requires generated answers.

Four gates are enforced, and all four are unconditional: `recall@5 >= 0.80`,
`precision@5 >= 0.60`, `refusal_accuracy >= 0.80`, and `false_accept_rate <= 0.10`.

## Frontend Test Coverage

Frontend tests are component-level tests:

| File | Test count | What it covers |
|---|---:|---|
| `AnswerView.test.tsx` | 1 | Answer text rendering |
| `CitationList.test.tsx` | 3 | Empty citation state, citation titles, and expandable citation detail |
| `QualityPanel.test.tsx` | 4 | Metrics rendering, running an audit, the disabled state when a deployment refuses audits, and the concurrent-audit message |
| `QueryBox.test.tsx` | 3 | Input rendering, trimmed submit behavior, and disabled loading state |

These tests cover component behavior, not browser-level end-to-end behavior.

## Current Gaps

The main gaps are:

- No numeric line or branch coverage report is configured for backend or frontend.
- No full live integration test starts FastAPI, Qdrant, Redis, Ollama, and the
  frontend proxy together.
- No deployment smoke test verifies a deployed revision's health, query, refusal,
  and frontend behavior.
- The frontend suite is not wired into CI, even though it now runs reproducibly
  from a committed lockfile.
- Backend has strong behavioral coverage, but no static type-checking gate yet.
- **The eval suites score retrieval, not generation.** `false_accept_rate` reads
  0.00 on all three sets, while a live query can still be accepted on weak
  evidence and refused only by the model. These numbers describe the retrieval
  decision, not what the user receives.

## Mentor-Facing Summary

The project has solid behavioral test coverage for the core RAG risks: chunking,
retrieval, citations, refusals, rate limiting, eval gates, runtime configuration,
and Qdrant client compatibility. Most backend tests are unit or mocked wiring
tests, with targeted integration-lite coverage through FastAPI `TestClient` and
in-memory Qdrant contract tests.

The project does not currently claim numeric test coverage. The next maturity step
would be to add coverage tooling, wire the frontend suite into CI, add full-service
integration tests, and add deployment smoke tests.
