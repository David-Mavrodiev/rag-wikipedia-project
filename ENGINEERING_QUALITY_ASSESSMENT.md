# Engineering Quality Assessment

This document evaluates the RAG Wikipedia project across testing, typing, architecture,
review workflow, security, dependency management, reliability, asynchronous processing,
load testing, and deployment operations.

## Project context

The application is an end-to-end Retrieval-Augmented Generation system over Wikipedia.
It uses:

- FastAPI for the backend API.
- React, TypeScript, and Vite for the frontend.
- Qdrant as the vector database.
- Ollama with `llama3.2:3b` for local generation.
- `BAAI/bge-small-en-v1.5` sentence embeddings.
- Prefect for ingestion pipeline orchestration.
- Docker Compose locally, with Azure Container Apps documented as the target cloud platform.

## Summary evaluation

| Area | Current maturity | Evaluation |
|---|---:|---|
| Unit and integration testing | Medium | Good backend and frontend unit coverage exists, plus evaluation tests for RAG quality. Full service-level integration and deployment smoke tests should be automated. |
| Typing and modular architecture | Medium-high | Backend uses Pydantic models and clear module boundaries. Frontend uses TypeScript. Stricter static checks would improve confidence. |
| Code review experience | Medium | The codebase is readable and split into reviewable modules. A PR template and review checklist would make reviews more consistent. |
| Authentication and authorisation | Low | No user authentication or role-based authorization is implemented. This is acceptable for a local/demo app but not for a public production API. |
| Dependency management | Medium | Python uses `uv` and a lockfile; frontend dependencies are declared. More version pinning and automated vulnerability checks are needed. |
| API error handling | Medium | Dependency failures map to clear `503` responses and request validation is handled by Pydantic/FastAPI. More structured error responses and global exception handling are recommended. |
| Asynchronous processing | Medium | FastAPI endpoints are async and ingestion uses Prefect. The query path still calls blocking retrieval and LLM operations inside an async route. |
| Load testing | Low-medium | Ingestion benchmarking exists, but API/frontend load tests are not yet documented or automated. |
| Secure secret management | Low-medium | Local config comes from environment variables and `.env`; Azure strategy avoids baked image secrets. Key Vault or identity-based secret access should be added for production. |
| Deployment and rollback after GitHub push | Medium | Azure Container Apps revision rollout and rollback are documented as a strategy. A GitHub Actions workflow should enforce tests, build images, deploy revisions, smoke test, and rollback automatically. |

## Unit and integration testing

### Current evidence

Backend tests are under `projects/rag-wikipedia/backend/tests/` and cover:

- API behavior: `test_api.py`, `test_health.py`.
- Retrieval and vector store behavior: `test_retrieval.py`, `test_vectorstore.py`.
- Prompt construction and citation extraction: `test_generation.py`.
- Refusal contract (hard, soft, and grounded-without-citations): `test_refusal.py`.
- Pipeline and chunking behavior: `test_pipeline.py`, `test_chunking.py`.
- Configuration and metrics: `test_config.py`, `test_metrics.py`.
- Evaluation harness and its gates: `test_run_eval.py`.
- Ingestion benchmark safety and collection ownership: `test_bench_ingest.py`.

The suite is **192 tests** as of this writing (`cd projects/rag-wikipedia/backend && uv run pytest -q`).

Frontend component tests are under `projects/rag-wikipedia/frontend/src/components/`
and use Vitest plus React Testing Library:

- `AnswerView.test.tsx`
- `CitationList.test.tsx`
- `QueryBox.test.tsx`

The main test commands are:

```bash
cd projects/rag-wikipedia
make test
cd frontend && npm test
```

The project also includes an evaluation harness:

```bash
cd projects/rag-wikipedia
make eval
```

This checks retrieval quality and refusal behavior against `backend/eval/golden.jsonl`.

### Evaluation

The unit test foundation is strong for a demo or portfolio project. The codebase tests
domain logic rather than only route status codes, which is important for a RAG system
where retrieval quality and refusal behavior are part of correctness.

The main gap is end-to-end integration coverage. The project should add automated tests
that start the API, Qdrant, Ollama or a test double, and the frontend proxy together,
then verify:

- `GET /health` returns `200`.
- `POST /query` returns a grounded answer for answerable fixture content.
- `POST /query` returns the explicit refusal shape for unanswerable questions.
- The frontend can submit a query through the nginx reverse proxy.
- Qdrant collection data persists across service restart in deployment-like conditions.

## Typing and modular architecture

### Current evidence

The backend separates responsibilities into focused packages:

- `app/api/` for route handlers.
- `app/models/` for Pydantic request and response models.
- `app/core/` for embeddings, retrieval, vector store, prompt construction, LLM access,
  citations, refusal behavior, and configuration.
- `pipeline/` for ingestion.
- `eval/` for quality evaluation.

The frontend separates the main app from presentational/query components under
`frontend/src/components/`.

Typing is present in both layers:

- Backend request and response schemas use Pydantic models.
- Python functions use type hints in core paths.
- Frontend uses TypeScript with `tsc` included in the production build.

### Evaluation

The architecture is modular and reviewable. API handlers are thin enough to understand
the request flow, while domain logic lives in core modules that can be tested without
HTTP. This is the right shape for a RAG application.

Recommended improvements:

- Add `mypy` or `pyright` to CI for backend static type checking.
- Keep Pydantic models as the API boundary and avoid returning untyped dictionaries
  from route handlers except for simple health checks.
- Add frontend API response types in a shared `types.ts` file rather than duplicating
  implicit response shapes across components.
- Prefer dependency injection for embedder, vector store, and LLM clients in tests
  instead of relying only on `lru_cache` constructors.

## Code review experience

### Current evidence

The codebase is split into small files with clear names. Tests are colocated by layer,
and operational documentation exists in `docs/runbook.md` and
`docs/deployment-strategy.md`.

### Evaluation

The review experience is good for code readability but should be formalized before
team or production use.

Recommended pull request checklist:

- Tests added or updated for changed behavior.
- `make test`, frontend tests, and lint pass.
- RAG evaluation still passes threshold gates.
- API response shape changes are documented.
- New environment variables are documented in README or deployment docs.
- Dependencies are justified and locked.
- Logs do not expose prompts, secrets, tokens, or sensitive user input.
- Deployment rollback path is known before merging.

Recommended repository additions:

- `.github/pull_request_template.md`
- `.github/workflows/ci.yml`
- CODEOWNERS for backend, frontend, and deployment-sensitive files if the project grows.

## Authentication and authorisation

### Current evidence

The current API exposes `/health` and `/query`. There is no authentication middleware,
token validation, session handling, API key requirement, or role-based authorization.
The deployment strategy recommends exposing only the frontend publicly and keeping the
API, Qdrant, and Ollama internal in Azure Container Apps.

### Evaluation

This is acceptable for local development and demos. It is not sufficient for a public
production deployment.

Minimum production requirements:

- Add authentication at the public entry point, such as OAuth/OIDC through the hosting
  platform, an identity-aware proxy, or application-level JWT validation.
- Protect `/query` from unauthenticated access.
- Keep `/health` either internal or split into public liveness and private readiness
  endpoints.
- Add authorization rules if different users or roles can access different corpora,
  indexes, models, or admin actions.
- Add rate limiting by identity or client IP to reduce abuse and cost spikes.

## Dependency management

### Current evidence

Python dependencies are declared in `projects/rag-wikipedia/backend/pyproject.toml`.
The workspace has `uv.lock`, which supports reproducible installs. Frontend
dependencies are declared in `frontend/package.json`.

The Qdrant client is bounded with a documented compatibility range:

```toml
qdrant-client>=1.12,<2
```

### Evaluation

The backend dependency story is stronger than the frontend because the Python side has
a lockfile in the repo. The frontend should also commit and maintain its package lock
file if not already present.

Recommended improvements:

- Commit `package-lock.json`, `pnpm-lock.yaml`, or `yarn.lock` for frontend
  reproducibility.
- Avoid `latest` container tags for production, especially `ollama/ollama:latest`.
- Use Dependabot or Renovate for dependency update PRs.
- Add vulnerability scanning in CI, such as `pip-audit` or `uv pip audit` where
  available, `npm audit`, and container image scanning.
- Keep dependency ranges narrow for packages that define external service contracts.

## API error handling

### Current evidence

The query endpoint:

- Validates request length with `QueryRequest.question`.
- Returns `503 Vector store unavailable` when retrieval fails.
- Returns `503 LLM unavailable` when generation fails.
- Returns a structured refusal response with `refused: true` when there is no relevant
  context.

### Evaluation

The API already distinguishes expected RAG refusal from infrastructure failure, which is
important. A refusal is a successful response, while Qdrant or Ollama downtime is a
service error.

Recommended improvements:

- Add a global exception handler that returns a consistent error envelope, for example
  `{ "error": { "code": "...", "message": "...", "request_id": "..." } }`.
- Add request IDs and include them in logs and error responses.
- Use `logger.exception(...)` for unexpected failures to retain stack traces.
- Add timeout handling around Qdrant and Ollama calls.
- Add readiness checks that verify Qdrant and Ollama connectivity, not only process
  liveness.
- Avoid logging full user prompts in production unless redaction and retention controls
  are in place.

## Asynchronous processing

### Current evidence

FastAPI route functions are declared with `async def`. The ingestion workflow lives
outside the request path under `backend/pipeline/` and uses Prefect-oriented pipeline
modules.

### Evaluation

The system separates long-running ingestion from interactive querying, which is the
right architectural decision. However, the query route currently calls retrieval and LLM
generation directly inside an async route. If those client calls are blocking, they can
reduce event-loop throughput under concurrent traffic.

Recommended improvements:

- Confirm whether Qdrant, embedding, and Ollama calls are blocking.
- If blocking, move them to a thread pool or use async client libraries.
- Add request timeouts and cancellation behavior.
- Use background jobs for ingestion, re-indexing, and large corpus refreshes.
- Expose job status for ingestion if users need to trigger it through an API.

## Load testing

### Current evidence

The repository includes ingestion benchmark coverage through `test_bench_ingest.py` and
`backend/eval/bench_ingest.py`. There is no dedicated API load test script or load test
report for `/query`.

### Evaluation

The project has started measuring ingestion performance but has not yet characterized
interactive query performance. For a RAG app, query latency is usually dominated by
embedding and LLM generation, so load tests should measure both average latency and tail
latency.

Recommended load test scenarios:

- Single-user baseline latency for answerable and unanswerable questions.
- Concurrent `/query` traffic at 5, 10, 25, and 50 virtual users.
- Cold-start behavior after API restart.
- Ollama model-loaded versus model-not-loaded behavior.
- Qdrant persistence and latency after real-profile ingestion.
- Frontend proxy throughput for `/query` and `/health`.

Recommended tools:

- Locust for Python-based user flows.
- k6 for HTTP-level smoke and load tests.
- Azure Container Apps revision metrics and Log Analytics for deployment load tests.

Success criteria should include:

- p95 latency target for `/query`.
- Error rate target under expected concurrency.
- Maximum acceptable refusal rate drift on known answerable questions.
- No container restarts or memory pressure during the test window.

## Secure secret management

### Current evidence

Runtime configuration is environment-variable driven through Pydantic settings.
The Azure deployment strategy avoids baking secrets into images and uses managed
identity for ACR pulls. It also notes that storage keys should be moved to Azure Key
Vault or replaced with identity-based access.

### Evaluation

This is a reasonable development posture, but production secret management is not fully
implemented.

Recommended production controls:

- Keep `.env` local only and never commit real secrets.
- Add `.env.example` with non-secret placeholders.
- Store cloud secrets in Azure Key Vault.
- Use managed identities wherever possible instead of static keys.
- Rotate any storage keys or tokens used during manual deployment.
- Do not expose Qdrant or Ollama publicly.
- Ensure logs do not contain secrets, raw authorization headers, or sensitive prompts.

## Deployment and rollback procedures after each push to GitHub

### Current evidence

`docs/deployment-strategy.md` describes Azure Container Apps, internal service
networking, managed identity image pulls, revision rollout, and traffic splitting.
The current repository does not include a GitHub Actions workflow that performs this
automatically after each push.

### Recommended post-push pipeline

**Not every push should reach production.** Split the pipeline by trigger, so that
unreviewed branch commits can never change production traffic:

| Trigger | Runs | Deploys? |
|---|---|---|
| Push to any branch, and every pull request | tests, lint, build, RAG eval gate | **No** |
| Merge to `main` (i.e. after review) | the same checks, then build and push images | **Staging only** |
| Manual approval on a protected `production` environment, or a version tag | promote the already-tested image | **Yes** |

Gate the production job behind a GitHub Environment with required reviewers, and give
it the only credentials that can touch production. A workflow triggered by `push` to
any branch with production secrets in scope is a supply-chain risk, not a convenience.

With that separation in place, the pipeline stages are:

1. Checkout source.
2. Install Python and Node dependencies from lockfiles.
3. Run backend lint:

   ```bash
   cd projects/rag-wikipedia
   make lint
   ```

4. Run backend tests:

   ```bash
   cd projects/rag-wikipedia
   make test
   ```

5. Run frontend tests and build:

   ```bash
   cd projects/rag-wikipedia/frontend
   npm ci
   npm test
   npm run build
   ```

6. Run RAG evaluation:

   ```bash
   cd projects/rag-wikipedia
   make eval
   ```

7. Build backend and frontend container images with immutable tags, preferably the
   Git commit SHA.
8. Push images to Azure Container Registry.
9. Deploy a new Azure Container Apps revision without immediately sending all traffic
   to it.
10. Run smoke tests against the new revision:

   - `GET /health`
   - one answerable `/query`
   - one unanswerable `/query`
   - frontend page load

11. Shift a small percentage of traffic to the new revision.
12. Monitor p95 latency, error rate, container restarts, and application logs.
13. Shift to 100 percent only after validation passes.

### Rollback procedure

If smoke tests or production monitoring fail after a push:

1. Identify the last healthy Container Apps revision:

   ```bash
   az containerapp revision list -g <resource-group> -n <app-name> -o table
   ```

2. Move traffic back to the healthy revision:

   ```bash
   az containerapp ingress traffic set \
     -g <resource-group> \
     -n <app-name> \
     --revision-weight <healthy-revision>=100
   ```

3. Confirm health:

   ```bash
   curl https://<frontend-fqdn>/health
   ```

4. Verify one answerable and one unanswerable query.
5. Keep the failed revision available for log inspection unless it is causing active
   harm.
6. Open a GitHub issue or revert PR with:

   - failing commit SHA
   - failed revision name
   - error symptoms
   - smoke test output
   - rollback time
   - suspected root cause

Rollback should be traffic-based first. Reverting code should happen after service is
stable unless the bad revision corrupted persistent data or introduced a security risk.

## Priority recommendations

1. Add GitHub Actions for backend tests, frontend tests, build, lint, and RAG eval.
2. Add deployment smoke tests and traffic-based ACA rollout/rollback automation.
3. Add authentication before exposing the app to untrusted users.
4. Add API request IDs, timeouts, structured errors, and readiness checks.
5. Add API load tests for `/query`.
6. Add Key Vault or managed-identity secret access for production.
7. Add backend static type checking and lock frontend dependencies.
