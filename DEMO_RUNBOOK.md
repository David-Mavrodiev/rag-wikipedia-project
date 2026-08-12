# DEMO RUNBOOK — run the RAG locally (interview / live demo)

How to bring the whole stack up on this Windows machine, warm it, demo it, and shut it
down. Every command and every flag here was verified end-to-end on branch `hadi-dev`.

**Read the flags, not just the commands.** The two `OLLAMA_*` variables are not
cosmetic — without them the model fails to load on a 16 GB machine (see
[Troubleshooting](#troubleshooting)).

---

## TL;DR — start

```powershell
# 1) Vector DB  (run from projects\rag-wikipedia)
docker compose start qdrant

# 2) LLM — CPU-only + bounded context (see WHY below)
$env:OLLAMA_NUM_GPU=0; $env:OLLAMA_CONTEXT_LENGTH=8192; ollama serve

# 3) API + demo console  (run from projects\rag-wikipedia\backend, separate terminals)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m http.server 5500 --directory ..\demo
```

Then open <http://localhost:5500/console.html> and **warm it** (below) before demoing.

---

## Prerequisites

| Needs | Check |
|---|---|
| Docker Desktop **running** | `docker version` — if it errors with a `//./pipe/dockerDesktopLinuxEngine` message, launch Docker Desktop and wait ~30 s |
| Ollama installed | `C:\Users\HADI\AppData\Local\Programs\Ollama\ollama.exe` |
| Model pulled | `ollama pull llama3.2:3b` (~2 GB, one time) |
| Backend venv | from `backend\`: `uv sync` (creates `.venv`) |
| Qdrant has data | see [Re-ingest](#re-ingest-when-the-index-is-empty) |

---

## Why those two Ollama flags

| Flag | Without it | Why |
|---|---|---|
| `OLLAMA_NUM_GPU=0` | `cudaMalloc failed: out of memory` → API returns **503 LLM unavailable** | The GPU is too full to hold the model; force CPU inference. |
| `OLLAMA_CONTEXT_LENGTH=8192` | `failed to allocate CPU buffer of size 12884901888` (12.9 GB) | `llama3.2:3b` advertises a **128k** context, so Ollama tries to allocate a huge KV cache. Retrieval only ever sends ~3 000 tokens (`token_budget`), so 8k is plenty. |

The Ollama **tray app auto-restarts a GPU-mode server**. If port 11434 is already taken
(`bind: Only one usage of each socket address...`), kill it first — otherwise you are
silently talking to the GPU server that will OOM:

```powershell
Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## Warm up before any live demo

Cold start is ~13 s; once the model is resident, queries drop to **2–5 s**. Never let a
panel watch the first cold query. Fire one throwaway request:

```powershell
curl.exe -s -m 300 -X POST http://127.0.0.1:8000/query `
  -H "Content-Type: application/json" `
  -d '{\"question\":\"Who was Abraham Lincoln?\"}'
```

Keep the browser tab open — after a long idle the model unloads and the next query is
slow again.

---

## Demo script (verified)

| Ask | Expected | Shows |
|---|---|---|
| **"Who was Abraham Lincoln?"** | grounded answer, **2–3 sources**, green `✓ ancrée` badge | retrieval + inline citations |
| **"Who won the 2022 FIFA World Cup?"** | `I don't know based on the provided context.` orange `refus contrôlé` badge | **the refusal path — the money shot** |

Other safe in-corpus topics: *Aristotle, anarchism, Andre Agassi, Alaska, algae, Apollo 8,
Ayn Rand*.

**Caveat to state honestly:** the live index holds only the **45 "A" articles** (984
vectors) from the bounded verification ingest, so anything else refuses. Frame it as
deliberate: *"a small slice makes the refusal behaviour easy to demonstrate."*

Avoid *"What was the Apollo 11 mission?"* — the 3B model sometimes echoes a literal `[n]`
placeholder instead of a real citation number. Harmless (correctly **not** a refusal) but
it looks scruffy on screen.

**The one-liner:** *"It answers only from the documents it was given, shows its sources,
and says 'I don't know' rather than inventing — want to see it refuse?"*

---

## TL;DR — stop

```powershell
# API (8000) + console (5500)
foreach ($p in 8000,5500) {
  (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue).OwningProcess |
    Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force

# Vector DB — `stop`, NOT `down`: keeps the volume (984 vectors) intact
docker compose stop qdrant
```

Docker Desktop keeps running with no containers; close it too if you need the RAM.

---

## Re-ingest (when the index is empty)

```powershell
# from projects\rag-wikipedia\backend, with qdrant up
.\.venv\Scripts\python.exe -m pipeline.flow      # full "tiny" profile: 500 articles
```

Ingestion is **idempotent** — point IDs are `sha256(source_id::chunk_index)[:32]`, so
re-running upserts in place instead of duplicating.

Verify:

```powershell
curl.exe -s http://localhost:6333/collections/wikipedia
```

---

## Troubleshooting

Every row below is an error actually hit on this machine.

| Symptom | Cause | Fix |
|---|---|---|
| `open //./pipe/dockerDesktopLinuxEngine` | Docker Desktop not running | Launch it, wait ~30 s |
| Qdrant container exits **101**, log says `unknown variant 'on_disk'` | Volume was written by Qdrant **1.9.2**; server is now **1.18.3** — incompatible segment format | `docker volume rm rag-wikipedia_qdrant_data`, `docker compose up -d qdrant`, then **re-ingest** |
| `/query` → **503 Vector store unavailable**, log: `'QdrantClient' object has no attribute 'query_points'` | `qdrant-client` pinned too old (`<1.10`); `vectorstore.py` uses the Query API | `pyproject.toml` must pin `qdrant-client>=1.12,<2` (fixed in commit `170e800`), then `uv sync` |
| `/query` → **503 LLM unavailable**, log: `cudaMalloc failed` | Ollama running in GPU mode, GPU full | Kill Ollama, restart with `OLLAMA_NUM_GPU=0` |
| `/query` → **503 LLM unavailable**, log: `failed to allocate CPU buffer of size 12884901888` | 128k-context KV cache | Restart with `OLLAMA_CONTEXT_LENGTH=8192` |
| `bind: Only one usage of each socket address` | Ollama tray app already holds 11434 | Kill `ollama*` processes, then `ollama serve` |
| `uv sync` "succeeds" but the old package is still imported | The running API **file-locks** `site-packages` on Windows | Stop the API **first**, then `uv pip install --reinstall <pkg>` |
| Console shows a correct answer as "refus contrôlé" | Stale `console.html` cached by the browser | Hard-refresh (**Ctrl+F5**) — the fix reads the backend `refused` flag |

---

## Ports & health

| Service | Port | Health check |
|---|---|---|
| Qdrant | 6333 | `curl.exe -s http://localhost:6333/` |
| Ollama | 11434 | `curl.exe -s http://localhost:11434/api/tags` |
| API | 8000 | `curl.exe -s http://127.0.0.1:8000/health` → `{"status":"ok"}` |
| Console | 5500 | <http://localhost:5500/console.html> |

---

## Related docs

- [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) — rebuild from scratch; **§17** = swapping the LLM/embedder provider
- [ANNOTATED_CODE.md](ANNOTATED_CODE.md) — every backend/frontend file, line-commented
- [TESTS_ANNOTATED.md](TESTS_ANNOTATED.md) — the test suite explained

Run the tests before trusting a demo: from `backend\`, `.\.venv\Scripts\python.exe -m pytest -q`
(55 passing as of the last verified run).
