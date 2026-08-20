# Codex Case Study Simulation: Trainer-Led RAG Repository Lab

> Current recording choice: use **add `groundedness` to the evaluation report** as
> the primary Codex feature, following `MASTER_TRAINER_PREP_PLAN.md`. The `/stats`
> endpoint described below remains a backup lab exercise for live diagnostics.

## Purpose

This document simulates a practical, role-relevant case study for a technical trainer interview. It uses this repository as the live training asset and assumes the interviewer asks you to teach a group of developers how to use Codex responsibly on an existing codebase.

The goal is not to show that Codex can write code. The goal is to show that you can lead a room through repository understanding, scoped delegation, implementation review, validation, and live troubleshooting.

## Scenario

You are facilitating a 45 to 60 minute technical lab for developers who are new to agentic coding workflows. The repository contains a Wikipedia RAG application with:

- FastAPI backend
- React and Vite frontend
- Qdrant vector store
- Ollama generation path
- BGE embedding path
- Retrieval, citations, refusal logic, tests, evals, and deployment notes

The simulated customer problem:

> The team has inherited a RAG application. They need to understand how it works, add one small diagnostic feature, validate the change, and learn how to recover when the lab breaks.

The trainer task:

> Demonstrate how to use Codex to inspect the repository, plan a narrow change, implement it, review the diff, run tests, troubleshoot one failure, and summarize the engineering workflow for participants.

## Interviewer Prompt

Use this as the simulated prompt from Matze or another interviewer:

> You are training a group of developers. Here is an existing repository. Show them how you would use Codex to understand the codebase and implement a small, production-relevant feature. Explain your choices as you go. The group should learn not only the commands, but also how to think about human review, test validation, and troubleshooting.

## Candidate Success Criteria

A strong demonstration should show that you can:

- Explain before demonstrating.
- Scope Codex work tightly.
- Ask Codex to inspect before editing.
- Use a plan before implementation.
- Review proposed changes instead of accepting blindly.
- Run the repository's checks.
- Diagnose failures by layer: configuration, dependency, retrieval, model, frontend, or test setup.
- Translate the engineering workflow into enterprise training guidance.

A weak demonstration looks like:

> I type a prompt, Codex generates code, and I accept the result.

A strong demonstration sounds like:

> Before I delegate this, I want Codex to inspect the repository and identify the right ownership boundary. Then I will review its plan, let it make a narrow change, inspect the diff, run tests, and only accept the result if the behavior is validated.

## Repository Orientation

Start from:

```powershell
cd C:\Users\HADI\Downloads\RAG-WIKIPEDIA-PROJECT\rag-wikipedia-project\projects\rag-wikipedia
```

Key files to mention:

| File | Why it matters |
|---|---|
| `backend/app/main.py` | FastAPI app setup and router registration |
| `backend/app/api/health.py` | Existing simple health endpoint |
| `backend/app/api/query.py` | Main RAG query endpoint |
| `backend/app/core/config.py` | Runtime settings such as `top_k`, `collection`, and `refusal_threshold` |
| `backend/app/core/vectorstore.py` | Qdrant collection and vector count behavior |
| `backend/tests/test_health.py` | Pattern for simple API endpoint tests |
| `backend/tests/test_api.py` | Query behavior tests with mocks |
| `Makefile` | Project commands for tests, linting, ingestion, and evals |

Repository shape:

```text
User question
  -> FastAPI /query
  -> Embed question
  -> Search Qdrant
  -> Filter retrieved chunks
  -> Build grounded prompt
  -> Generate answer
  -> Extract citations
  -> Return answer, citations, and refusal state
```

## Lab Feature

Use this scoped implementation task:

> Add a `GET /stats` endpoint for live lab diagnostics. It should return the configured collection name, `top_k`, `refusal_threshold`, embedding model, LLM model, expected embedding dimension, and vector count if Qdrant is reachable. If Qdrant is unavailable, the endpoint should still return configuration values and include a clear vector store status.

Why this feature is trainer-relevant:

- It helps participants debug whether the app is configured correctly.
- It exposes the difference between app configuration and dependency health.
- It is small enough for Codex to implement during a live lab.
- It can be validated with focused tests.
- It gives the trainer a practical troubleshooting anchor.

## Trainer Flow

### 1. Explain The Workflow

Say:

> I am going to use Codex as a development assistant, not as an autopilot. First I will ask it to inspect the repository without changing files. Then I will review its proposed plan. Only after the ownership boundary is clear will I let it implement a small change. At the end, we review the diff and run the project's checks.

Teaching point:

> The professional habit is not prompt-and-accept. The habit is context, scope, plan, implement, review, validate.

### 2. Ask Codex To Inspect Only

Suggested prompt:

```text
Inspect this RAG Wikipedia project without changing files. Identify the backend API entrypoints, settings module, vector store abstraction, and existing test patterns. Then propose the smallest implementation plan for adding a GET /stats diagnostics endpoint.
```

Expected Codex investigation:

- Finds `backend/app/main.py`.
- Finds existing routers under `backend/app/api`.
- Finds `settings` in `backend/app/core/config.py`.
- Finds `QdrantStore.count()` and `EXPECTED_DIM` in `backend/app/core/vectorstore.py`.
- Finds API tests under `backend/tests`.
- Proposes a small router and tests.

Trainer narration:

> Notice that I did not ask for code yet. I asked for orientation and a plan. This makes Codex surface its assumptions before it touches the repository.

### 3. Review The Plan

Acceptable plan:

- Add `backend/app/api/stats.py`.
- Include the router in `backend/app/main.py`.
- Use `settings` for static configuration values.
- Use `QdrantStore(settings.qdrant_url, settings.collection).count()` for vector count.
- Catch vector store errors and report an unavailable status.
- Add focused tests that mock Qdrant behavior.

Reject or revise a plan that:

- Rewrites the query endpoint.
- Adds a new framework.
- Calls the LLM for diagnostics.
- Requires a live Qdrant instance for unit tests.
- Changes ingestion or retrieval behavior.
- Hides dependency failures behind a generic 500.

Trainer line:

> A good Codex plan is small, testable, and aligned with existing ownership boundaries. If it wanders into unrelated architecture, I stop and rescope.

### 4. Let Codex Implement

Suggested implementation prompt:

```text
Implement the /stats endpoint using the plan. Keep the change narrow. Follow the existing FastAPI router pattern. Add focused tests that do not require live Qdrant. Run the relevant backend tests and lint if available.
```

Expected touched files:

```text
backend/app/main.py
backend/app/api/stats.py
backend/tests/test_stats.py
```

Expected response shape:

```json
{
  "collection": "wikipedia",
  "top_k": 5,
  "refusal_threshold": 0.3,
  "embed_model": "BAAI/bge-small-en-v1.5",
  "llm_model": "llama3.2:3b",
  "expected_embedding_dim": 384,
  "vector_store": {
    "status": "ok",
    "vector_count": 500
  }
}
```

If Qdrant is unavailable:

```json
{
  "collection": "wikipedia",
  "top_k": 5,
  "refusal_threshold": 0.3,
  "embed_model": "BAAI/bge-small-en-v1.5",
  "llm_model": "llama3.2:3b",
  "expected_embedding_dim": 384,
  "vector_store": {
    "status": "unavailable",
    "vector_count": null
  }
}
```

### 5. Review The Diff

Use this review checklist:

- Did the change stay inside the backend API layer?
- Did it preserve `/health` and `/query` behavior?
- Are settings read from `settings` rather than duplicated constants?
- Is the vector store failure handled clearly?
- Do tests mock Qdrant rather than depend on Docker?
- Is the endpoint useful for live troubleshooting?
- Is any sensitive information exposed?

Trainer line:

> Diagnostics should reveal operational state without exposing secrets. Model names and collection names are useful. API keys, raw prompts, and credentials are not.

### 6. Run Validation

Run from:

```powershell
cd C:\Users\HADI\Downloads\RAG-WIKIPEDIA-PROJECT\rag-wikipedia-project\projects\rag-wikipedia
```

Useful checks:

```powershell
make test
make lint
```

If Make is not available on the participant machine, use direct commands:

```powershell
cd backend
uv run pytest tests/ -v
uv run ruff check app/ pipeline/ eval/ tests/
```

Optional manual check with the backend running:

```powershell
curl.exe -s http://127.0.0.1:8000/stats
```

Trainer line:

> Validation depends on the risk of the change. For this endpoint, focused API tests and lint are enough. If we changed retrieval or prompt construction, I would also run the eval suite.

## Live Troubleshooting Drill

After the implementation, simulate one failure.

### Failure Option A: Qdrant Is Down

Symptom:

```text
/health returns ok, but /query fails or /stats reports vector_store.status = unavailable
```

Teaching point:

> App health and dependency health are different. FastAPI can be alive while Qdrant is unavailable.

Diagnosis:

```powershell
docker compose ps
curl.exe -s http://localhost:6333/
```

Fix:

```powershell
docker compose up -d qdrant
```

### Failure Option B: Test Import Error

Symptom:

```text
ModuleNotFoundError
```

Teaching point:

> The first question is whether we are running tests from the expected working directory with the expected environment.

Diagnosis:

```powershell
Get-Location
Get-ChildItem
```

Fix:

```powershell
cd C:\Users\HADI\Downloads\RAG-WIKIPEDIA-PROJECT\rag-wikipedia-project\projects\rag-wikipedia\backend
uv run pytest tests/ -v
```

### Failure Option C: Embedding Dimension Mismatch

Symptom:

```text
Expected embedding dim=384, got 1536
```

Teaching point:

> A vector index is created for one embedding dimension. If you switch embedding models, you usually need a new collection or a re-ingest.

Likely cause:

- Local BGE embeddings use 384 dimensions.
- A hosted embedding model may use a different dimension.
- Existing Qdrant collection vectors and new query vectors no longer match.

Fix options:

- Reset or recreate the collection.
- Use a collection name per embedding model.
- Re-ingest the corpus with the selected embedder.
- Keep generator swaps separate from embedding swaps.

### Failure Option D: LLM Is Unavailable

Symptom:

```text
503 LLM unavailable
```

Teaching point:

> This is a dependency failure, not necessarily a prompt or application logic failure.

Diagnosis:

```powershell
curl.exe -s http://localhost:11434/api/tags
```

Fix:

```powershell
$env:OLLAMA_NUM_GPU=0
$env:OLLAMA_CONTEXT_LENGTH=8192
ollama serve
```

## Audience Interaction Prompts

Use these questions during the lab:

- What context did Codex need before it could safely edit?
- Which files are the ownership boundary for this change?
- What would make this task too broad?
- What should we verify before accepting the diff?
- Why should tests mock Qdrant for this endpoint?
- What information is safe to expose in diagnostics?
- If the tests fail, do we change the code, the test, or the environment?
- What would you tell a developer who accepts every Codex diff without review?

## Expected Human Judgments

You should explicitly accept:

- A small router-based endpoint.
- Tests that mock vector count success and failure.
- Clear dependency status reporting.
- Reuse of existing settings and vector store abstraction.

You should explicitly reject:

- A broad refactor of app startup.
- A new dependency for simple diagnostics.
- Live-service-dependent unit tests.
- Secret exposure.
- Silent exception swallowing with no status.
- Changes to retrieval behavior unrelated to `/stats`.

## Suggested Timing

| Segment | Time | Trainer Goal |
|---|---:|---|
| Frame the case study | 5 min | Explain the repo, audience, and trainer objective |
| Repository inspection | 8 min | Ask Codex to inspect without edits |
| Plan review | 7 min | Accept, reject, or narrow Codex's proposal |
| Implementation | 10 min | Let Codex make the scoped change |
| Diff review | 8 min | Model human ownership |
| Tests and lint | 7 min | Validate before accepting |
| Troubleshooting drill | 10 min | Diagnose one realistic failure |
| Wrap-up | 5 min | Translate the workflow into training principles |

## Assessment Rubric

| Area | Strong Signal | Weak Signal |
|---|---|---|
| Framing | Explains what Codex is doing and why | Jumps straight into prompting |
| Scoping | Keeps the task narrow and testable | Lets Codex redesign unrelated parts |
| Repository understanding | Names concrete files and responsibilities | Speaks generically about "the backend" |
| Plan review | Challenges assumptions before editing | Accepts the first plan automatically |
| Implementation control | Uses existing patterns | Introduces unnecessary architecture |
| Validation | Runs focused tests and lint | Relies on visual inspection only |
| Troubleshooting | Diagnoses by system layer | Randomly changes prompts or code |
| Enterprise readiness | Mentions review, secrets, traceability, and repeatability | Treats AI output as inherently correct |

## Closing Script

Use this closing summary:

> In this lab, Codex helped us move faster, but it did not remove engineering responsibility. We scoped the task, inspected the repository, reviewed a plan, implemented a small endpoint, checked the diff, and validated the behavior. When something failed, we diagnosed the layer instead of guessing. That is the workflow I would teach developers: use Codex for acceleration, keep humans accountable for correctness.

## Preparation Notes

Before running this in an interview setting:

- Watch a technical Codex training session for facilitation style, not only product mechanics.
- Practice explaining each step before executing it.
- Prepare one failure in advance so troubleshooting feels deliberate.
- Keep the implementation target small.
- Do not paste real credentials into prompts, terminals, slides, or recordings.
- Have a fallback path if Docker, Ollama, or dependency installation fails.

## One-Minute Personal Positioning

Use this if asked why the case study is relevant:

> I chose this repository because it is realistic enough to teach meaningful engineering habits: API boundaries, configuration, vector search, model dependencies, tests, and evals. The case study is not about showing that Codex can generate code. It is about showing that I can help developers use Codex safely and effectively on an existing codebase, while keeping review, validation, and troubleshooting central to the workflow.
