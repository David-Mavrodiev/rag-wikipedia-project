# Serving fixes - citations, startup, keep-alive

> **Status: point-in-time record, 22 Sep 2026.** Three fixes to problems the
> [measurement baseline](measurement-baseline.md) found in the serving path,
> measured on branch `feat/serving-fixes` at `89e9826` (the baseline plus these
> changes, and nothing in retrieval). Where this disagrees with the repository,
> the repository is right.

## What changed

| problem the baseline found | change | where |
|---|---|---|
| The prompt said "Cite inline with [n] markers", and llama3.2:3b copied `[n]` literally in 8 of 55 answers. Such an answer reached the UI with no source and nothing saying so | The prompt shows real numbers, `[1]` or `[2]`, and says a citation is a chunk number, never a letter. The response carries `cited`, and the UI says when an answer cites nothing | `app/core/prompt.py`, `app/models/query.py`, `AnswerView.tsx` |
| The first request after a start took 75.4 s: 60.8 s loading the embedder, which checked the Hugging Face Hub even with the model cached, and 13.2 s loading the LLM | Both load in a background thread at startup, the embedder from the local cache first; `/health` answers while they load | `app/main.py`, `app/api/query.py`, `app/core/embeddings.py` |
| Ollama unloads a model after five idle minutes, and the next answer pays the reload. The two slowest answers of the baseline run (64.4 s and 35.3 s) came right after pauses longer than that | Every request sends `keep_alive` (`LLM_KEEP_ALIVE`, default `30m`) | `app/core/llm.py`, `app/core/config.py` |

Both startup settings are documented in `backend/.env.example` and can be turned
off (`WARMUP_ON_STARTUP=false`).

## Measured

Startup, same machine and model cache, before and after:

| | before | after |
|---|---:|---:|
| first answer after a start | 75.4 s | 8.0 s |
| `deps` stage of that first request | 60.8 s | 0.0 ms |
| Hugging Face Hub requests to load the cached embedder | 29 (17.95 s) | 0 |

The load has not disappeared: it moved to startup, where it runs in the
background (on 22 Sep: embedder 9.7 s, vector store 0.3 s, LLM 9.9 s) while the
API already answers `/health`.

Serving latency, `make bench-latency`: 80 distinct questions from the serving
suites against the running API, paced at 90/86 C
([`backend/eval/latency/serving.md`](../backend/eval/latency/serving.md)):

| group | n | p50 | min | max |
|---|---:|---:|---:|---:|
| generated (all at a clamped clock) | 68 | 7.06 s | 1.74 s | 14.1 s |
| refused before retrieval (intent filter) | 12 | < 1 ms | - | - |

Stage split of the generated requests, p50: embed 26 ms, search 51 ms, retrieve
88 ms, generate 6,957 ms, total 7,061 ms. Generation is 98.5% of an answer, so
retrieval changes such as reranking are measured against ~7 s, not against ~90 ms.

This bench does not test the keep-alive fix: its longest cooldown pause was 101 s,
short of the five minutes after which Ollama unloads by default, so no reload
could happen with or without it. Its p50 matches the baseline run's 7.3 s at the
same clamped clock.

## Not yet measured

- **The placeholder rate after the prompt change.** Repeating the end-to-end run
  on this branch (80 generations, about 1.5 h paced on the reference laptop) is
  what shows whether `[n]` is gone. Until then the change is covered by tests
  only.
