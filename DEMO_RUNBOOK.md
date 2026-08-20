# DEMO RUNBOOK — run the RAG locally (interview / live demo)

How to bring the whole stack up on this Windows machine, warm it, demo it, and shut it
down. Every command and every flag here was verified end-to-end on branch `hadi-dev`.

**Read the flags, not just the commands.** The two `OLLAMA_*` variables are not
cosmetic — without them the model fails to load on a 16 GB machine (see
[Troubleshooting](#troubleshooting)).

---

## TL;DR — start

```powershell
# 1) Vector DB + rate-limiter store  (run from projects\rag-wikipedia)
#    `up -d` creates the containers if they do not exist yet AND starts them, so
#    this works on a fresh checkout. (`start` only resumes existing ones.)
#
#    Redis is NOT optional here: RATE_LIMIT_ENABLED defaults to true, and the
#    rate-limit middleware answers POST /query with 503 when it cannot reach
#    Redis. Skipping this line makes every demo query fail.
docker compose up -d qdrant redis

# 2) LLM — plain `ollama serve` is enough (see WHY below). The API pins its own
#    context window on every request (LLM_NUM_CTX, default 8192), so generation
#    no longer depends on env vars set in this particular terminal.
ollama serve

# 3) API + demo console  (run from projects\rag-wikipedia\backend, separate terminals)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m http.server 5500 --directory ..\demo
```

> **No Redis on the demo machine?** Turn the limiter off instead of leaving it
> pointed at a host that is not there — `$env:RATE_LIMIT_ENABLED="false"` in the
> terminal that runs uvicorn. The limiter is skipped entirely and `/query`
> answers normally.

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

## Why no Ollama flags any more

`llama3.2:3b` advertises a **128k** context, and Ollama sizes its buffers from
that: ~12.9 GB on CPU, or a CUDA compute buffer that will not fit a 6 GB card
(`cudaMalloc failed: out of memory`). Either way the API returns **503 LLM
unavailable**. Retrieval only ever sends ~3 000 tokens (`token_budget`), so the
huge window was never needed.

This used to be fixed with two environment variables. It is now fixed in the
code: `OllamaLLM` sends `num_ctx` with every request (`LLM_NUM_CTX`, default
8192), so a plain `ollama serve` works and the app behaves identically however
Ollama was started.

**Do not set `OLLAMA_NUM_GPU=0`.** It was over-prescribed: it avoids the OOM
only by abandoning the GPU. Measured on the RTX 3060 Laptop (6 GB), same prompt:

| configuration | result |
|---|---|
| bounded context, GPU | **7.3 s** |
| CPU-only (`num_gpu=0`), default context | 70.4 s |
| default context, GPU | OOM → 503 |

Bounded context is what avoids the failure; CPU-only just costs you 10x.

The Ollama **tray app auto-restarts a GPU-mode server**. If port 11434 is already taken
(`bind: Only one usage of each socket address...`), kill it first — otherwise you are
silently talking to the GPU server that will OOM:

```powershell
Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## Warm up before any live demo

**The first query after a fresh `ollama serve` is slow — measured between ~13 s and
~69 s on this machine** (the 3B model is read from disk into RAM, and the API loads
the embedder on its first call). Once the model is resident, queries drop to
**2–5 s**. Never let a panel watch that first cold query. Fire one throwaway request
and wait for it to return before you present:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post `
  -ContentType 'application/json' `
  -Body '{"question":"Who was Abraham Lincoln?"}'
```

> **Use `Invoke-RestMethod`, not `curl.exe`, for POSTs here.** Windows PowerShell
> mangles quotes when passing arguments to native executables — verified on this
> machine, `-d '{\"question\":\"Who was Abraham Lincoln?\"}'` arrives truncated at
> the first space, and the unescaped variant arrives with its quotes stripped.
> Either way the API gets invalid JSON, the request 422s, and the model stays
> cold. `Invoke-RestMethod` is native PowerShell, so it never crosses that
> boundary. If you must use curl, put the body in a file and pass `-d "@body.json"`.

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

## Reset the Qdrant volume (destructive)

Needed when the stored data was written by an **incompatible Qdrant version** —
the container then panics at boot with ``unknown variant `on_disk` `` and exits
**101**. There is no in-place migration; the volume has to go.

```powershell
# from projects\rag-wikipedia
docker compose stop qdrant
docker compose rm -f qdrant     # REQUIRED: Docker refuses to remove a volume
                                # that any container still references, even a
                                # stopped one ("volume is in use")
docker volume rm rag-wikipedia_qdrant_data
docker compose up -d qdrant
```

Then **re-ingest** (previous section) — the new volume is empty.

> **Never use `docker compose down -v` for this.** The `-v` flag removes *every*
> volume in the project, including `ollama_data` (your ~2 GB `llama3.2:3b`
> model) and `hf_cache` (the embedding model). You would be re-downloading
> gigabytes to fix a Qdrant problem. Remove the one named volume instead.

---

## Troubleshooting

Every row below is an error actually hit on this machine.

| Symptom | Cause | Fix |
|---|---|---|
| `open //./pipe/dockerDesktopLinuxEngine` | Docker Desktop not running | Launch it, wait ~30 s |
| Qdrant container exits **101**, log says `unknown variant 'on_disk'` | Volume was written by Qdrant **1.9.2**; server is now **1.18.3** — incompatible segment format | See [Reset the Qdrant volume](#reset-the-qdrant-volume-destructive) — the container must be removed **before** the volume |
| `/query` → **503 Vector store unavailable**, log: `'QdrantClient' object has no attribute 'query_points'` | `qdrant-client` pinned too old (`<1.10`); `vectorstore.py` uses the Query API | `pyproject.toml` must pin `qdrant-client>=1.12,<2` (fixed in commit `170e800`), then `uv sync` |
| `/query` → **503 LLM unavailable**, log: `cudaMalloc failed` or `failed to allocate CPU buffer of size 12884901888` | Ollama sizing buffers for the model's 128k context | Should not happen: the API sends `num_ctx` itself. If it does, something is overriding it — check `LLM_NUM_CTX` and that `OllamaLLM.generate` still passes `options` |
| `bind: Only one usage of each socket address` | Ollama tray app already holds 11434 | Kill `ollama*` processes, then `ollama serve` |
| `uv sync` "succeeds" but the old package is still imported | The running API **file-locks** `site-packages` on Windows | Stop the API **first**, then `uv pip install --reinstall <pkg>` |
| Console shows a correct answer as "refus contrôlé" | Stale `console.html` cached by the browser | Hard-refresh (**Ctrl+F5**) — the fix reads the backend `refused` flag |

---

## ⚠️ Deploying to Azure Container Apps

`infra/aca/backing-apps.json` pins `qdrant/qdrant:v1.18.3` against a persistent
`qdrant-data` volume. **The same version-skip rule applies as locally, but the
consequences are worse:** if that volume already holds data written by v1.9.2,
the container starts on incompatible storage, and Qdrant's storage migration is
**not reversible**.

Before deploying over an existing volume, do one of:

- **Reindex into a new volume** (recommended) — provision a fresh `qdrant-data`,
  deploy v1.18.3, re-run ingestion, then switch traffic; or
- **Staged upgrade** — snapshot, then move through each intermediate minor
  version (latest patch of each), verifying health at every step.

The warning lives here rather than in the JSON because ARM templates are parsed
by tooling that does not reliably accept comments.

## Ports & health

| Service | Port | Health check |
|---|---|---|
| Qdrant | 6333 | `curl.exe -s http://localhost:6333/` |
| Ollama | 11434 | `curl.exe -s http://localhost:11434/api/tags` |
| API | 8000 | `curl.exe -s http://127.0.0.1:8000/health` → `{"status":"ok"}` |
| Console | 5500 | <http://localhost:5500/console.html> |

---

## Related docs

- [CODEX_RUNBOOK.md](CODEX_RUNBOOK.md) — Codex CLI environment + the `codex_apps` startup fix
- [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) — rebuild from scratch; **§17** = swapping the LLM/embedder provider
- [ANNOTATED_CODE.md](ANNOTATED_CODE.md) — every backend/frontend file, line-commented
- [TESTS_ANNOTATED.md](TESTS_ANNOTATED.md) — the test suite explained

Run the tests before trusting a demo: from `backend\`, `.\.venv\Scripts\python.exe -m pytest -q`
(55 passing as of the last verified run).
