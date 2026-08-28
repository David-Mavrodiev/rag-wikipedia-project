# Pertinent Problem/Fix Notes

This file captures concrete problems found during project work and the fixes that
resolved them. Keep entries short enough to scan, but specific enough that the same
failure mode is recognizable later.

## Codex MCP startup interrupted: `codex_apps`

### Problem

Launching Codex repeatedly printed:

```text
MCP startup interrupted. The following servers were not initialized:
  codex_apps
```

Codex still worked, but startup was slow and noisy. The root cause was the remote
ChatGPT Apps connector layer loading a large tool payload at startup, compounded by
a stale `node_repl` MCP entry pointing at an obsolete Codex binary.

### Preferred fix

The detailed runbook and recovery checklist live in `CODEX_RUNBOOK.md`.

If app-backed functionality must stay available, keep apps enabled and remove stale
local MCP entries first:

```powershell
codex mcp remove node_repl
codex features enable apps
codex doctor
```

What we found on 2026-08-19, on **Codex CLI v0.147.0** (`codex --version`): the
commands below and the top-level `apps` setting are valid for that version — check
`codex features --help` before assuming they still are on a newer one, since this
surface has moved before. `codex features enable apps` is the right final state
when those Codex app/plugin capabilities need to remain available. Confirm it by
checking that `~/.codex/config.toml` contains:

```toml
apps = true
```

If the warning persists, clear the stale apps caches listed in `CODEX_RUNBOOK.md`,
then run `codex doctor` again.

Fallback only: `codex features disable apps` removes the `codex_apps` startup payload,
but it also removes Codex access to the ChatGPT Apps connector layer for Sites, Gmail,
Slack, Drive, Calendar, and GitHub. Use that fallback only when startup reliability is
more important than app-backed functionality.

## Benchmark collection ownership race

### Problem

The ingestion benchmark accepted a Qdrant collection name through `BENCH_COLLECTION`.
It checked whether that collection already existed, then created it, and finally
dropped it in cleanup.

That check-then-create sequence was unsafe. Another process could create the same
collection name after the existence check but before creation. If the benchmark then
continued and reached cleanup, it could delete a collection it did not actually own.

The risky path lived in:

- `projects/rag-wikipedia/backend/eval/bench_ingest.py`

### Fix

Collection ownership is now established atomically by creation itself:

- `claim_collection()` calls Qdrant `create_collection()` directly.
- A successful create proves this run owned the name **at the moment of creation** —
  which is what closes the original check-then-create race. It is not a guarantee
  that the name is still ours at cleanup: another process could delete and recreate
  it in between. That residual window is accepted rather than fixed, because the
  default collection name is a per-run UUID that nothing else has a reason to touch.
- If creation fails and the collection exists, the benchmark exits with a refusal
  message instead of running.
- If creation fails for another reason, such as a bad config or unavailable server,
  the original error is re-raised instead of being hidden.
- Cleanup only runs after `claim_collection()` succeeds.

Regression coverage was added in:

- `projects/rag-wikipedia/backend/tests/test_bench_ingest.py`

The tests cover:

- a free benchmark collection name succeeds;
- an already-taken collection name is refused;
- genuine non-ownership failures are propagated.

### Verification

Run the focused tests from the backend directory:

```bash
cd projects/rag-wikipedia/backend
uv run pytest tests/test_bench_ingest.py
```

---

## Thermal shutdown kills the ingest — and it looks like Docker crashing

### Problem

Long ingests died repeatedly, and each time the symptom was Docker:

```text
error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/info":
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

The engine would also disappear *between two consecutive commands* — a check would
report it up, and the next command a few seconds later would fail to connect.

**Docker is not crashing.** The laptop is powering itself off to protect the
hardware, and Docker Desktop does not start on boot. Everything else — the
detached ingest process, Qdrant, the background monitors — dies with the machine.

Confirmed in the Windows System log (three unclean shutdowns in 24 h):

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008; StartTime=(Get-Date).AddDays(-1)}
#   10:57:38  Id=41      Id 41   = kernel-power, rebooted without a clean shutdown
#   02:46:27  Id=41      Id 6008 = the previous shutdown was unexpected
#   02:13:47  Id=41
```

Contributing conditions on this machine (ASUS TUF F15, i7-11800H + RTX 3060 6 GB):

- GPU sustained at **93–97 °C**, clocked down to **210 MHz of 2100** (thermal cap)
- CPU reported at **96 °C** with both fans at 5000 RPM
- GPU **idles at 89–91 °C** with no load at all
- WSL2 (Docker) competing for RAM; only ~1.5 GB free at one point

The GPU draws only ~24–30 W of its ~95 W budget at 93 °C, so the cooling — not
the silicon — is the limit.

### Why this matters more than it looks

At 210 MHz the pipeline ran at **1 chunk/s**; cold it ran at **24**. So the
machine spends most of a long job in its slowest state, which *lengthens* the job,
which keeps it hot. Sustained max-load is self-defeating here.

### Recovery procedure

Do it as **one command**, not several. The engine can die in the gaps between
separate invocations, and a `docker compose start` issued into a dead engine
fails in a way that looks like data loss but is not.

```bash
SP=/path/to/scratch

# 1. Start Docker Desktop ITSELF - probing the engine is not enough, and the
#    app does not start on boot.
if ! docker info >/dev/null 2>&1; then
  powershell -NonInteractive -Command \
    "if (-not (Get-Process 'Docker Desktop' -EA SilentlyContinue)) { Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }"
fi

# 2. Wait until the engine SERVES, not merely answers. `docker info` can succeed
#    with an empty ServerVersion while the daemon is still coming up; requiring
#    a non-empty `docker volume ls` is the reliable readiness signal.
for i in $(seq 1 100); do [ -n "$(docker volume ls -q 2>/dev/null)" ] && break; sleep 5; done

# 3. Qdrant, then wait for its HTTP API (container "Started" != API ready).
cd projects/rag-wikipedia && docker compose start qdrant
for i in $(seq 1 60); do [ -n "$(curl -s -m 3 http://localhost:6333/collections)" ] && break; sleep 3; done

# 4. Verify the data BEFORE relaunching, and only relaunch if it is really there.
V=$(curl -s -m 10 http://localhost:6333/collections/wikipedia | grep -oE '"points_count":[0-9]+' | cut -d: -f2)
[ -n "$V" ] || { echo "qdrant unreachable - do NOT relaunch"; exit 1; }

# 5. Resume. No special flag: the flow skips what is already stored.
cd backend && PROFILE=real COLLECTION=wikipedia TQDM_DISABLE=1 \
  nohup uv run --no-sync python pipeline/flow.py > "$SP/ingest.log" 2>&1 &
```

### Why the data survives

Qdrant lives in the named volume `rag-wikipedia_qdrant_data`, which persists
across reboots. Verified after a hard shutdown: both collections came back
`green` with the exact counts they had.

Resuming costs nothing because ingestion is resumable — `existing_ids()` asks
Qdrant which point IDs it already holds and only the missing chunks are embedded.
Embedding is ~97% of ingestion time, so a resume is effectively free. Observed
across four separate shutdowns: **not one chunk was re-embedded.**

The remaining risk is not lost work but **corruption**: a hard power cut during a
Qdrant write could damage a segment. It has not happened in four shutdowns, but
it is the reason to prefer finishing sooner over running hot indefinitely.

### `uv run` silently reverts a manually installed torch

Unrelated to heat but discovered alongside it. CUDA torch installed with
`uv pip install` is **not** in `uv.lock`, so any plain `uv run` re-syncs the
environment and puts the CPU build back:

```text
Installed 1 package in 20.49s     # <- this is uv undoing the CUDA install
CUDA available: False | 2.12.1+cpu
```

Use `uv run --no-sync` for every command once a CUDA wheel is installed
out-of-band. Reinstalling is fast (~26 s) if the wheel is still in uv's cache.

### Mitigations, cheapest first

1. **Let it cool between segments.** Since the GPU is clamped to 210 MHz once
   hot, a duty cycle can deliver *higher* average throughput than running flat
   out, and keeps the machine below the shutdown threshold.
2. **Cooling pad / elevate the chassis / clean the fans.** Idling at 89 °C
   suggests airflow or thermal paste, not workload.
3. **Undervolt the CPU**, which is the larger heat source under this mixed load.
4. **Move long ingests off this machine.** The real fix: an hour of rented GPU
   finishes in minutes what takes this laptop a day.
