# Master Trainer Lab: Grounded RAG With OpenAI, Codex, and Live Troubleshooting

## Purpose

This lab is designed for an OpenAI Master Trainer interview or technical enablement session. It demonstrates the ability to teach a practical AI implementation, guide mixed technical audiences, use Codex CLI as a development assistant, and stay composed when the lab environment fails.

The lab uses this repository's Wikipedia RAG application as the teaching asset:

- FastAPI backend
- Qdrant vector database
- Local or OpenAI embedding provider
- Local or OpenAI generation provider
- Retrieval, citations, refusal logic, tests, evals, and a runbook

The trainer outcome is not only "the app works." The real outcome is that participants understand how a grounded AI system is built, how it fails, and how to debug it systematically.

## Audience

Primary audience:

- Solution architects
- AI engineers
- Technical consultants
- Partner enablement teams

Secondary audience:

- Sales engineers
- Business stakeholders
- Delivery managers who need to understand tradeoffs without reading every line of code

## Trainer Positioning

Use this framing at the start:

> In this lab we will build and inspect a grounded AI workflow. The model does not answer from memory first. It retrieves relevant source chunks, builds a constrained prompt, answers with citations, and refuses when the evidence is not present. This is the core pattern behind many enterprise AI assistants.

Then make the relevance explicit:

> The important skill is not memorizing one RAG implementation. The important skill is knowing which layer is responsible for which behavior, and how to diagnose failures quickly in front of a room.

## Learning Objectives

By the end of the lab, participants should be able to:

1. Explain the difference between prompting, RAG, fine-tuning, and tool calling.
2. Trace a query through the full RAG path: request, embedding, retrieval, prompt construction, generation, citation extraction, and refusal handling.
3. Identify why embedding dimension mismatches require re-indexing.
4. Use Codex CLI to inspect a repo, propose a focused change, run tests, and review a diff.
5. Diagnose common lab failures: missing API key, rate limit, empty vector store, wrong model provider, malformed output, and unavailable local services.
6. Translate the same technical system into a business explanation about trust, traceability, latency, cost, and governance.

## Recommended Duration

Total: 90 minutes

| Segment | Time | Goal |
|---|---:|---|
| Context and architecture | 10 min | Explain the business problem and system shape |
| Local run and first query | 15 min | Show a grounded answer with citations |
| Refusal path | 10 min | Show why controlled refusal matters |
| Codex CLI task | 20 min | Make the embedding dimension configurable |
| OpenAI provider discussion | 15 min | Explain provider swap and embedding dimensions |
| Troubleshooting drill | 15 min | Diagnose staged failures |
| Recap and business translation | 5 min | Convert engineering details into enterprise value |

## Prerequisites

Participant machine:

- Git
- Python 3.12
- Docker Desktop
- `uv`
- PowerShell
- OpenAI API key, if running the OpenAI provider path
- Codex CLI installed and authenticated, if running the Codex section live

All paths in this lab are relative to the project directory. From your clone:

```powershell
cd <repo-root>/projects/rag-wikipedia
```

## Source Files To Know

| File | Why it matters |
|---|---|
| `backend/app/api/query.py` | API entrypoint for `/query` |
| `backend/app/core/embeddings.py` | Embedding interface and local embedder |
| `backend/app/core/llm.py` | LLM interface and local Ollama implementation |
| `backend/app/core/providers.py` | Provider registry intended for OpenAI/Azure/local swaps |
| `backend/app/core/vectorstore.py` | Qdrant collection, search, and vector count |
| `backend/app/core/retrieval.py` | Retrieval threshold and empty-result behavior |
| `backend/app/core/prompt.py` | Grounding prompt construction |
| `backend/app/core/refusal.py` | Controlled refusal detection |
| `backend/eval/run_eval.py` | Evaluation gate for retrieval and refusal quality |
| `DEMO_RUNBOOK.md` | Machine-specific startup and troubleshooting notes |

## Part 1: Explain The Architecture

Use this short explanation for engineers:

> The backend receives a question, embeds it, searches Qdrant for similar chunks, filters by relevance, builds a grounded prompt from retrieved context, calls the LLM, extracts citation markers, and returns a response object with answer, citations, and a refused flag.

Use this short explanation for business stakeholders:

> The assistant is designed to answer only from approved source material. If the documents do not contain the answer, it should say that instead of inventing. That gives us traceability, safer deployment, and a clearer path to audit.

Whiteboard flow:

```text
User question
  -> FastAPI /query
  -> Embed question
  -> Search Qdrant
  -> Select top chunks
  -> Build grounded prompt
  -> Generate answer
  -> Extract citations
  -> Return answer or refusal
```

## Part 2: Start The Local Lab

Run Qdrant (`up -d` creates the container on a first run; `start` only resumes an
existing one, so it fails for a participant who has never run Compose here):

```powershell
docker compose up -d qdrant
```

Start Ollama in CPU mode for this machine:

```powershell
$env:OLLAMA_NUM_GPU=0
$env:OLLAMA_CONTEXT_LENGTH=8192
ollama serve
```

Start the backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the demo console:

```powershell
cd ..
python -m http.server 5500 --directory demo
```

Open:

```text
http://localhost:5500/console.html
```

Health check:

```powershell
curl.exe -s http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

## Part 3: Warm The Model

Before presenting, warm the model:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post `
  -ContentType 'application/json' `
  -Body '{"question":"Who was Abraham Lincoln?"}'
```

Trainer note:

> Always warm the model before a live demo. Cold start latency is not a conceptual failure, but if you let the room watch a cold first request, they may interpret infrastructure delay as model uncertainty.

## Part 4: Show A Grounded Answer

Ask:

```text
Who was Abraham Lincoln?
```

Expected behavior:

- The answer is grounded in retrieved Wikipedia chunks.
- The response includes citations.
- The UI shows a grounded answer state.

Teaching points:

- Embeddings retrieve semantically similar text, not exact keyword matches only.
- Citations are not generated magically; the backend extracts citation markers from the model output.
- Retrieval quality depends on chunking, embedding model, corpus coverage, and score thresholds.

## Part 5: Show A Controlled Refusal

Ask one of the questions the evaluation set already marks
`"expected_refusal": true` in `backend/eval/golden.jsonl`:

```text
What did I have for breakfast this morning?
```

Use a golden unanswerable case rather than an ad-hoc question (an earlier draft
used "Who won the 2022 FIFA World Cup?"): a question about the world can become
*answerable* the moment the indexed corpus changes, which makes a live demo
nondeterministic. A question about the participant's private life can never be
in the corpus, so the refusal is deterministic — and it ties the demo directly
to the `refusal_accuracy` metric the eval gate measures.

Expected behavior:

- Retrieval finds nothing above the score threshold, so the API returns a hard
  refusal with `"refused": true` and an empty `citations` list.
- The answer is exactly:

```text
I don't know based on the provided context.
```

Teaching points:

- Refusal is a product feature, not a failure.
- In enterprise RAG, a correct refusal can be more valuable than a fluent unsupported answer.
- The retrieval layer can trigger a hard refusal when evidence is absent or weak.
- The LLM can also produce a soft refusal when context is insufficient.

Business translation:

> This is how we reduce hallucination risk. We make the system evidence-seeking first, and we make unsupported answers visible instead of hiding uncertainty behind fluent language.

## Part 6: Codex CLI Exercise

Goal:

Use Codex CLI to make the vector store's embedding dimension configurable.
This is a real, still-open repo gap: `backend/app/core/vectorstore.py` hardcodes
`EXPECTED_DIM = 384`, which blocks swapping to any other embedder.

> **Baseline note.** An earlier version of this lab used "add groundedness to the
> eval report". That has since been implemented and refined (commits `8b595e5`,
> `a9aac36`), so it is no longer an open gap — it now serves as the *review* case
> study (what the agent produced, what it cost, and the flag it was scoped behind).
> Demo an unimplemented feature so the exercise is real.

Suggested prompt to Codex CLI:

```text
Inspect this FastAPI RAG project without editing first. Explain how
backend/app/core/vectorstore.py decides the embedding dimension, where the value comes
from, and what happens today if the embedder produced a different size. Then implement a
minimal change making it configurable via an EMBED_DIM environment variable, defaulting
to 384 so current behaviour is unchanged, validated against a real embedding before the
collection is created. Add focused tests. Do not touch retrieval, chunking, or the API.
```

Trainer commentary while Codex works:

> Notice the workflow. I am not asking Codex to redesign the system. I am giving it a narrow, testable eval change. I still review the diff and run the tests. This is the right mental model for coding agents in professional delivery: acceleration with human ownership.

Expected Codex workflow:

1. Inspect `app/main.py`, router files, config, and vectorstore.
2. Propose a minimal eval runner change.
3. Add test coverage for the new report field and unchanged gate behavior.
4. Run the backend test suite.
5. Present a diff for review.

Useful trainer questions:

- What context did Codex need before editing?
- What should require approval?
- Which files did it touch?
- Did it preserve existing eval gates?
- What tests prove the change?
- What would you reject in the generated diff?

## Part 7: OpenAI Provider Swap Discussion

This repository contains a provider registry concept in:

```text
backend/app/core/providers.py
```

The intended design:

```text
LLM_CHOICE=ollama-3b | ollama-1b | openai | azure
EMBED_CHOICE=bge | openai | azure
```

Local path:

```text
BGE embeddings -> 384-dimensional vectors
Ollama llama3.2:3b -> local generation
```

OpenAI path:

```text
OpenAI embeddings -> usually different vector dimension
OpenAI model -> hosted generation
```

Critical teaching point:

> You can swap the generator without rebuilding the vector index. But if you swap the embedding model and the vector dimension changes, you must recreate or separate the Qdrant collection and re-ingest the corpus.

Example environment shape:

```powershell
$env:LLM_CHOICE="openai"
$env:EMBED_CHOICE="openai"
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_MODEL="gpt-5"
$env:OPENAI_EMBED_MODEL="text-embedding-3-small"
$env:EMBED_DIM="1536"
```

Trainer note:

> Do not paste real API keys into slides, prompts, logs, or shared terminals. Use environment variables or a secrets manager. Treat key handling as part of the lab, not an administrative detail.

## Part 8: Troubleshooting Drill

Use this as the live failure playbook. The goal is to model calm diagnosis, not to memorize fixes.

### Failure: API Key Missing

Symptom:

```text
401 Unauthorized
Incorrect API key provided
```

Likely cause:

- `OPENAI_API_KEY` is missing from the current shell.
- The key was set in another terminal.
- The app was started before the environment variable was set.

Diagnosis:

```powershell
$env:OPENAI_API_KEY
```

Fix:

```powershell
$env:OPENAI_API_KEY="sk-..."
```

Then restart the backend.

Trainer line:

> Environment variables are process-scoped. Setting a key in one terminal does not update a server already running in another process.

### Failure: Rate Limit Or Quota

Symptom:

```text
429 Too Many Requests
rate_limit_exceeded
insufficient_quota
```

Likely cause:

- Too many requests per minute.
- Too many tokens per minute.
- Account or project quota is exhausted.
- Ingestion is embedding too many chunks concurrently.

Fix options:

- Reduce batch size.
- Add exponential backoff.
- Lower concurrency.
- Use batch processing for offline ingestion.
- Switch to a smaller model for the lab.
- Check project limits and billing.

Trainer line:

> A 429 is not solved by changing the prompt. It is a throughput and quota problem, so the fix belongs in batching, backoff, concurrency, or account configuration.

### Failure: Embedding Dimension Mismatch

Symptom:

```text
Expected embedding dim=384, got 1536
```

or Qdrant rejects vectors during upsert/search.

Likely cause:

- The collection was created for BGE embeddings.
- The app is now using OpenAI embeddings.
- Existing vectors and new query vectors have different dimensions.

Diagnosis:

```powershell
curl.exe -s http://localhost:6333/collections/wikipedia
```

Fix:

- Use a separate collection for each embedding dimension.
- Set `EMBED_DIM` correctly.
- Recreate the collection.
- Re-ingest the corpus with the selected embedder.

Trainer line:

> Vector databases are strict about dimensions. A 384-dimensional index and a 1536-dimensional query vector are different coordinate systems.

### Failure: Empty Or Weak Retrieval

Symptom:

```text
I don't know based on the provided context.
```

for a question you expected to be answerable.

Likely cause:

- Corpus slice does not contain the answer.
- Ingestion did not run.
- Query uses vocabulary far from the indexed text.
- `refusal_threshold` is too high.
- `top_k` is too low.

Diagnosis:

```powershell
curl.exe -s http://localhost:6333/collections/wikipedia
```

Check vector count and collection name.

Fix options:

- Re-ingest.
- Ask an in-corpus question.
- Increase corpus profile.
- Tune chunking.
- Adjust `top_k`.
- Review retrieval scores before changing thresholds.

Trainer line:

> Do not immediately blame the LLM. In RAG, many bad answers are retrieval problems.

### Failure: LLM Unavailable

Symptom:

```text
503 LLM unavailable
```

Likely cause with local Ollama:

- Ollama server is not running.
- Model is not pulled.
- GPU memory is insufficient.
- Context window allocation is too large.

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

Trainer line:

> The API returned 503 because its dependency failed. The right move is to check the dependency health, not rewrite application code.

### Failure: Vector Store Unavailable

Symptom:

```text
503 Vector store unavailable
```

Likely cause:

- Qdrant is stopped.
- Wrong Qdrant URL.
- Docker Desktop is not running.
- Client/server version mismatch.

Diagnosis:

```powershell
docker compose ps
curl.exe -s http://localhost:6333/
```

Fix:

```powershell
docker compose up -d qdrant
```

Trainer line:

> Separate app health from dependency health. FastAPI can be alive while Qdrant is down.

### Failure: Context Length Exceeded

Symptom:

```text
context_length_exceeded
maximum context length
```

Likely cause:

- Too many retrieved chunks.
- Chunks are too large.
- Prompt template is verbose.
- Conversation history was included without trimming.

Fix options:

- Lower `top_k`.
- Reduce chunk size.
- Add token budgeting.
- Summarize long context before generation.
- Keep only relevant conversation state.

Trainer line:

> Context is a budget. Retrieval should spend that budget on the highest-value evidence.

### Failure: Malformed Model Output

Symptom:

```text
JSON parse error
missing citation marker
unexpected answer shape
```

Likely cause:

- Free-form generation was expected to behave like structured data.
- Prompt instructions are underspecified.
- Parser is too brittle.

Fix options:

- Use structured outputs where appropriate.
- Validate model output.
- Fail gracefully.
- Separate answer text from machine-readable metadata.

Trainer line:

> If downstream code requires structure, enforce structure at the model boundary and still validate defensively.

### Failure: Browser Shows Stale State

Symptom:

- UI shows old labels.
- Backend response is correct but frontend display is wrong.

Likely cause:

- Browser cache.
- Old frontend server still running.

Fix:

```text
Ctrl+F5
```

or restart the frontend/demo server.

Trainer line:

> Always verify the backend response directly before assuming the frontend logic is wrong.

## Part 9: Enterprise Decision Framework

Use this scenario:

> A customer has 20,000 internal HR documents. Should they fine-tune a model or use RAG?

Recommended answer:

> Start with RAG. The content changes, the company needs source traceability, and HR answers require policy freshness. Fine-tuning is better for behavior, tone, format, or specialist task patterns. It is not the primary way to inject frequently changing facts.

Decision table:

| Need | Best first option | Why |
|---|---|---|
| Fresh policy knowledge | RAG | Update documents and re-index |
| Citations | RAG | Answers can point to sources |
| Brand voice | Prompting or fine-tuning | Mostly behavior/style |
| Strict output schema | Structured outputs | Machine-readable contract |
| External system action | Tool calling / agents | Model chooses when to call tools |
| Offline large ingestion | Batch processing | Better throughput and cost control |
| High-risk domain | RAG + evals + guardrails | Evidence and measurable gates |

## Part 10: Evaluation Discussion

This repository already includes eval assets:

```text
backend/eval/golden.jsonl
backend/eval/run_eval.py
backend/eval/metrics.py
```

Explain:

> A demo proves the happy path once. Evals prove whether the system continues to work across a representative set of questions.

Metrics to discuss:

- Recall at k: did retrieval find the right evidence?
- MRR: how high did the right evidence rank?
- Refusal accuracy: did the system refuse unsupported questions?
- Latency: can users tolerate the response time?
- Cost per query: can the use case scale economically?

Trainer line:

> In enterprise enablement, evals are how we move from impressive demos to repeatable delivery.

## Part 11: Wrap-Up Script

Use this closing summary:

> We saw the full RAG path: ingestion, embeddings, vector search, grounded generation, citations, refusal, diagnostics, and evals. We also saw that many failures are not model failures. They come from environment configuration, retrieval quality, provider mismatch, rate limits, or missing dependencies. A strong AI practitioner can explain the architecture, but a strong trainer can help a room recover when something breaks.

## Interview Talking Points

Use these if asked why this lab is relevant to the Master Trainer role:

- It is hands-on and instructor-led.
- It works for engineers and business stakeholders.
- It includes live troubleshooting.
- It demonstrates OpenAI API readiness through provider abstraction.
- It demonstrates Codex CLI readiness through a small, testable repo change.
- It teaches enterprise-relevant themes: grounding, citations, refusals, evals, cost, latency, and governance.
- It is repeatable across cohorts because it has a runbook, tests, and known failure modes.

## Personal Positioning Line

Use this with Matze:

> I built this RAG project end to end, including ingestion, retrieval, prompt construction, refusal handling, citations, evals, deployment notes, and troubleshooting. I am now using it as a training lab and porting it toward OpenAI providers with Codex CLI, because the role is not only about knowing AI concepts. It is about helping professionals build, debug, and trust these systems in live sessions.

## References For Trainer Preparation

- OpenAI Partner Network announcement: https://openai.com/index/introducing-openai-partner-network/
- OpenAI developer documentation: https://developers.openai.com/
- OpenAI API quickstart: https://platform.openai.com/docs/quickstart/make-your-first-api-request
- OpenAI model documentation: https://developers.openai.com/api/docs/models
- OpenAI files and vector store API reference: https://platform.openai.com/docs/api-reference/files
- OpenAI vector store files API reference: https://platform.openai.com/docs/api-reference/vector-stores-files
