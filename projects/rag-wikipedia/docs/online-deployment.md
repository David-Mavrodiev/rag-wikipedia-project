# Putting this RAG online for €0, on the JobHack model

> **Status: proposal, 1 Oct 2026.** It replaces
> [deployment-strategy.md](deployment-strategy.md) (Azure Container Apps), which
> is kept as the record of an earlier decision. Two things forced the rewrite:
> that plan put `/qdrant/storage` on Azure Files, and **Qdrant's own install
> notes say a network filesystem will not work**; and Azure Container Apps bills
> by the hour, while this project needs to cost nothing while nobody is looking
> at it.
>
> The design below is the one already proven in the owner's JobHack project:
> **one image, two hosts** - Oracle Always Free (CPU, always on) and Modal (GPU,
> scaled to zero) - **a router that picks per request**, and **a cost policy
> enforced in code and in CI**, not by good intentions.

## 1. What "online" has to mean here

This repository is a portfolio piece for a job search, so "online" means three
things, in this order:

1. **A URL anyone can open** and ask a question of 24,694 Wikipedia articles,
   with sources shown, and a refusal when the corpus does not hold the answer.
2. **The numbers are public too.** The measurement reports
   ([measurement-baseline.md](measurement-baseline.md), the gates, the
   end-to-end run) published as pages, red gates included. The honesty is the
   product.
3. **It costs essentially nothing when idle**, and cannot be made expensive by
   a stranger with a loop.

## 2. Where each piece runs

| Piece | Host (free tier) | Why there |
|---|---|---|
| Frontend (Vite build, static) | **Cloudflare Pages** | free, commercial use allowed, global CDN, no server to keep warm |
| API (FastAPI), retrieval, refusal gate | **Oracle Always Free A1** (arm64) | always on, no cold start, and the gate's work is milliseconds of CPU |
| Vector store (Qdrant, 87,173 chunks) | **Oracle A1, local block volume** | **332 MB measured** - and Qdrant must have a local filesystem |
| Rate limiter (Redis) | **Oracle A1**, same compose | already how the app works; loopback only |
| Embeddings (bge-small, 384-d) | **Oracle A1** | 26 ms per query measured; CPU is fine |
| Generation (llama3.2:3b) | **Oracle A1 CPU** *or* **Modal GPU**, per request | the router's decision (§4) |
| Evaluation runs, audits, re-scoring | **Modal worker** (§5) | takes the laptop out of the loop |
| CI, lint, tests, docs-check | **GitHub Actions** | already there |
| Reports, demo console, corpus snapshot | **Cloudflare R2** or a GitHub release asset | free egress for the 328 MB snapshot |

**The only cash cost is a domain name, about €10-15 a year.** Everything else
sits inside a free tier, and the paid escape hatch (Modal beyond its credit) is
capped in §6.

### 2.1 One image, two hosts

Exactly JobHack's `services/inference` pattern: a single Dockerfile with a
`gateway-dev` target (API against an Ollama elsewhere - the laptop) and a
`serving` target (API + Ollama + baked model). Oracle runs it under
`docker compose` behind **Caddy** for automatic TLS; Modal runs the *same* image
on a GPU. Models are **baked into the image** with a pinned digest, so a cold
start loads weights from the image and never downloads them, and `models.lock`
records the digest that was measured.

Two facts make this project easier than JobHack: there is **one model**
(llama3.2:3b, already the measured configuration) and **no OCR**. The hard part
is the corpus, which JobHack does not have.

### 2.2 The corpus is the one real deployment problem

- **Measured: 332 MB** of Qdrant storage for 87,173 chunks, snapshot 328 MB.
  That fits in Oracle A1's block storage many times over, and even in RAM.
- **Never on a network filesystem.** Qdrant's docs rule out NFS and SMB; this is
  the mistake the Azure plan made.
- **Ship it as a snapshot, do not re-ingest in the cloud.** Ingestion is hours of
  embedding; a snapshot restore is one HTTP call. Publish
  `wikipedia-<date>.snapshot` to R2, restore at first boot, verify
  `points_count == 87173` and the IDF table before serving traffic.
- **A rebuild stays reproducible**: `make ingest` on a bigger machine, snapshot,
  publish, restore. The pipeline is already idempotent and resumable.

## 3. Answer latency, honestly

Measured on the reference laptop: retrieval 88 ms p50, generation 6,957 ms p50
on a thermally clamped GPU (2.9-4.9 s on a cool one). Generation is **98.5% of
an answer**, so the host that runs the model decides the user's wait.

**Unmeasured, and the first thing to measure:** llama3.2:3b on 4 Ampere A1 cores,
with this project's ~3,000-token context. Prompt prefill, not token generation,
is what hurts on CPU. JobHack's nearest data point is Qwen3-4B taking ~70 s per
screenshot on 2 OCPU. If the measurement comes back at a minute per answer, the
CPU path is a **queue with a notice**, not the interactive path - and that
decides how the router is tuned, not whether the design works.

Two levers exist if CPU proves too slow, both already in the code: `TOP_K` and
`TOKEN_BUDGET` (fewer, shorter chunks for the free CPU path), measured against
the suites like any other change, never guessed.

## 4. The router: who answers each request

A pure function, tested like `refusal.py`, fed by live signals (Oracle queue
depth × measured seconds per answer, whether Modal is warm, today's Modal spend,
host health):

| # | Situation | Host | Why |
|---|---|---|---|
| 1 | Demo question, **Modal warm right now** | **Modal** | the idle window is already paid for; it costs busy seconds, not a cold start |
| 2 | Demo question, Modal cold, **daily budget left**, Oracle ETA > 20 s | **Modal** (with a cold-start notice) | spend the credit where waiting hurts |
| 3 | Demo question, Oracle ETA ≤ 20 s | **Oracle** | free, always warm |
| 4 | **No budget left today** | **Oracle queue** + honest ETA | the service degrades in speed, never in truth |
| 5 | Evaluation / audit / re-scoring job | **Modal worker** (§5) | batch work, never on the interactive path |
| 6 | One host down | the other | each is the other's fallback |

The refusal gate runs on Oracle **before** either host is chosen: a question the
corpus cannot answer never reaches a GPU. That is a cost lever as much as a
quality one - and on the served suites the gate refuses a third of questions.

## 5. The worker, online

Today every evaluation runs on the laptop, paced by GPU temperature, and a
96 °C guard has stopped it twice. On Modal that becomes an ordinary job:

| Function | Trigger | What it does |
|---|---|---|
| `evaluate` | manual / `workflow_dispatch` | retrieval suites + end-to-end run, writes reports to R2 and opens a PR with the updated report files |
| `audit` | cron, nightly | `eval/audit.py` on the fixture corpus; fails loudly if a gate moves |
| `rescore` | manual | `run_e2e.py --score-only` after a judge change - the job that takes 25 minutes of laptop CPU |
| `wake` | HTTPS, bearer token | starts a run from CI or a PR comment |

The thermal pacer (`eval/thermal.py`) stays for laptop runs; on Modal the pacing
constraint is **cost**, and the same report records which host produced each
number. An end-to-end run that takes ~1.5 h paced on the laptop takes minutes on
a T4 - which is what makes "measure every change" realistic rather than
aspirational.

## 6. Cost policy (JobHack's, applied here)

**Levers, all in the Modal app file:**

- `min_containers=0` - never pay for idle. An always-on T4 is ~$580/month.
- `max_containers=1` to start (this is a demo, not a product): a burst queues
  instead of burning credit. JobHack uses 2.
- `scaledown_window` **120 s daytime, 60 s at night** (Europe/Paris), set by a
  cron `retune`, exactly as JobHack does. Each burst is billed as *cold start +
  work + the window*; 120 s keeps a reading session warm, 300 s would spend the
  credit twice as fast.
- **Models baked in**, so a cold start is weights-from-image, not a download.
- `OLLAMA_NUM_PARALLEL` + concurrency, so one warm GPU serves several visitors.

**Budgets, enforced in two independent places:**

| Limit | Where | Value |
|---|---|---|
| Per-request pacer | API, before routing | `daily_budget = credits_left / days_left_in_month` (≈ $1/day), demo traffic may use 60% of it |
| Daily hard ceiling | GitHub Actions watchdog on **Modal's real bill** | $3/day; over it, `modal app stop`, redeploy next morning |
| Per visitor | existing Redis limiter | 10 questions/minute, burst 20, plus a daily cap per IP |
| Workspace | Modal spend limit | the account-level stop, set to the credit |

The pacer works on our estimates; the watchdog works on Modal's invoice. Keeping
both is the point: a gap between them is a bug, and it shows up daily in the CI
summary.

**When the credit runs out, the service does not go down.** It falls back to
Oracle CPU with a visible notice. That is the whole reason for two hosts.

## 7. Security and privacy

- Admin endpoints (`/quality/audit?auto_correct=true`) stay behind
  `QUALITY_ADMIN_TOKEN`, **empty in production** so the auto-correcting path is
  closed outright.
- Qdrant and Redis listen on the compose network only; nothing but Caddy is
  published.
- `ALLOWED_ORIGINS` set to the Pages domain, not `*`.
- A gateway token between the API and the Modal endpoint (JobHack's
  `GATEWAY_TOKEN`), created as a Modal secret from CI and never printed.
- Questions are user input: log them with a retention window and say so on the
  page, or hash them. No accounts, no cookies, no analytics beyond Cloudflare's
  cookieless counter.

## 8. Rollout, each step ending in something visible

| Phase | Work | Visible result |
|---|---|---|
| 0 | Measure llama3.2:3b on an A1 shape (one afternoon, free tier) | the number §3 is missing |
| 1 | `serving` Dockerfile target, compose + Caddy, snapshot restore at boot | the API answering on a domain, CPU only |
| 2 | Frontend to Cloudflare Pages, `ALLOWED_ORIGINS`, demo console + reports published | the public URL for the LinkedIn post |
| 3 | Modal app (same image, GPU, scale-to-zero) + router + pacer | fast answers while credit lasts, honest fallback |
| 4 | Modal worker: nightly audit, on-demand evaluation, PR with reports | the laptop leaves the loop |
| 5 | Watchdog workflow, spend limits, daily cost summary in CI | a bill that cannot surprise |

Phases 1-2 are the ones that make it "online"; 3-5 are what make it survivable.

## 8a. What is already in the repository (1 Oct)

Phases 1, 4 and 5 have their parts written and tested where testing is possible
without an account:

| File | What it is | Verified |
|---|---|---|
| `backend/Dockerfile` | two targets: `dev` (the local stack) and `serving` (Oracle and Modal), the embedder baked in, `HF_HUB_OFFLINE=1`, non-root, healthcheck | **not built yet**: the laptop's disk is full (3.5 GB free of 476 GB) and the image is ~2 GB |
| `infra/oracle/compose.yml`, `Caddyfile`, `.env.example` | the always-on host: API, Qdrant on a LOCAL volume, Redis, Ollama, Caddy for TLS; only Caddy is published | `docker compose config` accepts it |
| `scripts/corpus_snapshot.py` | `create` / `download` / `restore` / `verify` - ship the corpus as a file | **round-trip run for real**: a 2,422-point collection snapshotted (19.1 MB) and restored into a fresh collection, verified; `verify` refuses the wrong size, a yellow status and a 768-d collection |
| `infra/modal/inference.py` | the GPU half: scale-to-zero, one container, day/night scaledown, model baked in | not deployed (no account yet) |
| `infra/modal/worker.py` | nightly audit, on-demand evaluation, `--score-only` re-scoring, a `wake` endpoint | not deployed |
| `infra/modal/watchdog.py` + `.github/workflows/modal-watchdog.yml` | the daily read of Modal's real bill; over the ceiling it stops the apps | 11 tests; the workflow skips cleanly while Modal is unconfigured |

What is deliberately NOT written yet: the **router and the pacer** (§4, §6).
They are serving-path code, and they need a measured number to be tuned
against - the one phase 0 produces. Writing them before that would be inventing
thresholds.

## 9. What is needed from the owner

- An **Oracle Cloud** account upgraded to Pay-As-You-Go (stays €0 inside the
  Always Free shapes, and PAYG gets the larger A1 shape), in an EU region.
- A **domain** (the only cash cost), on **Cloudflare** DNS.
- A **Modal** account; its monthly credit is the GPU budget.
- GitHub secrets: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_GATEWAY_TOKEN`,
  plus the R2 credentials for the snapshot.

## 10. Risks, named

| Risk | Mitigation |
|---|---|
| A1 CPU too slow for an interactive answer | measure first (phase 0); if slow, the CPU path becomes a queue with an ETA, and Modal serves the demo |
| Oracle Always Free capacity reclaimed (it is best-effort) | the snapshot lives in R2; a rebuild is `compose up` plus a restore |
| Modal credit exhausted mid-month | pacer degrades to Oracle; the service stays up, slower |
| A stranger loops the demo | Redis limiter per IP per minute and per day, `max_containers=1`, watchdog ceiling |
| The corpus snapshot drifts from the reports | the report already records `vector_count` and the collection; the boot check refuses to serve a collection that does not match |
