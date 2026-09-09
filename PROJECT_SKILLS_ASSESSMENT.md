# Project Skills Assessment

> **Status: point-in-time record, 18 Aug 2026.** Kept as evidence of what was
> true then, not maintained against the current code. Where it disagrees with
> the repository, the repository is right. Living references:
> `README.md`, `DEMO_RUNBOOK.md`, `ANNOTATED_CODE.md`, `TESTS_ANNOTATED.md`.

This note captures the evaluation of the RAG Wikipedia project against four practical AI engineering skills:

1. Building and deploying AI applications.
2. Mastering software engineering fundamentals.
3. Working effectively with programming agents.
4. Defining what should be built.

Overall assessment: **7.5/10**.

## 1. Building and Deploying AI Applications

Score: **8/10**

The project demonstrates a complete RAG application flow:

- Wikipedia ingestion.
- Text cleaning and token-based chunking.
- Embeddings with `BAAI/bge-small-en-v1.5`.
- Vector retrieval with Qdrant.
- Generation with Ollama and `llama3.2:3b`.
- Cited answers.
- Explicit refusal behavior when context is insufficient.
- FastAPI backend.
- React and Vite frontend.
- Docker Compose local deployment.
- Azure Container Apps deployment strategy.
- Evaluation gates for retrieval and refusal behavior.

The strongest signal is that the project treats **citations**, **refusal**, and **evaluation** as first-class application behavior, not optional demo features.

Remaining gaps:

- Deployment is documented but not fully automated.
- No GitHub Actions CI/CD workflow is present yet.
- No authentication, authorization, or rate limiting is implemented.
- Some deployment documentation needs consolidation to avoid drift between older prompts, current code, and the latest strategy.

## 2. Software Engineering Fundamentals

Score: **7.5/10**

The backend is clearly structured:

- `app/api/` for route handlers.
- `app/models/` for Pydantic request and response schemas.
- `app/core/` for retrieval, vector store, embeddings, prompts, citations, LLM access, refusal behavior, and configuration.
- `pipeline/` for ingestion.
- `eval/` for RAG quality evaluation.
- `tests/` for backend coverage.

The project uses typed Python, Pydantic, Docker, health checks, a `uv`-managed Python environment, and a meaningful backend test suite.

Verified during review:

```text
62 backend tests passed
```

Important engineering strengths:

- Explicit `refused` field in the API response.
- Separation between infrastructure failures and valid RAG refusals.
- Deterministic chunk point IDs for idempotent ingestion.
- Qdrant client/server compatibility documented and tested.
- Evaluation gates prevent weak RAG quality from silently passing.

Remaining gaps:

- Frontend tests could not be run because the local `vitest` binary was unavailable.
- Frontend dependency reproducibility should be improved with a committed lockfile.
- Backend static typing should be enforced with `mypy` or `pyright`.
- The async FastAPI query route calls blocking embedding, retrieval, and LLM work.
- API error handling should add request IDs, structured error envelopes, global exception handling, and explicit timeouts.

## 3. Working With Programming Agents

Score: **8/10**

The project is unusually well prepared for agent-based development.

Strong evidence:

- `PROJECT_BLUEPRINT.md` can be used to rebuild the project from scratch.
- Implementation, deployment, case-study, and trainer documents provide context for agents.
- The documentation captures failure modes, dependency compatibility traps, operational lessons, and exact architectural decisions.
- The project gives agents the kind of context they need: architecture, commands, expected behavior, known bugs, and acceptance checks.

This maps directly to effective use of tools such as Codex, Claude Code, Cursor, and similar programming agents.

Remaining gap:

- Agent-facing documentation must remain synchronized with the code. If deployment prompts or strategy docs drift, agents can confidently implement outdated instructions. The project should keep one canonical runbook as the source of truth.

## 4. Defining What Should Be Built

Score: **7/10**

The project has a clear technical product definition:

- An eval-first RAG system over a Wikipedia subset.
- Grounded answers with citations.
- Controlled refusal when retrieved evidence is weak or absent.
- Local-first open-source stack.
- Deployment path to Azure.

The scope choices are sensible:

- No LangChain or LlamaIndex dependency in v1.
- No hybrid search or reranking yet.
- No auth, billing, or streaming ingestion in the first version.
- Reliability is prioritized over answering every question.

Remaining product gap:

The target user and workflow should be made more explicit. Today, the project is a strong technical portfolio and RAG engineering demo. It would become stronger as a product if it clearly chose one primary use case, such as:

- Private knowledge-base question answering.
- Research assistant over a controlled corpus.
- RAG evaluation teaching tool.
- Client-ready template for document search.

## Priority Improvements

To move the project closer to **9/10**, prioritize:

1. Add GitHub Actions for backend tests, frontend tests, linting, builds, and RAG evaluation.
2. Make frontend tests reproducible with a committed lockfile and `npm ci`.
3. Add production API basics: authentication, rate limiting, request IDs, timeouts, and structured errors.
4. Add automated deployment smoke tests.
5. Consolidate deployment documentation into one canonical source of truth.
6. Add backend static type checking.
7. Clarify the target user and primary product workflow.

## Final Takeaway

This project already demonstrates the core skills expected from an AI application engineer: building a complete RAG pipeline, evaluating model behavior, handling refusals, exposing an API and frontend, and thinking through deployment.

The next level is mostly operational maturity: CI/CD, security, reproducibility, deployment automation, and tighter product positioning.
