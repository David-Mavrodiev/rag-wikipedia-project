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

What we found on 2026-08-19: `codex features enable apps` is the right final state
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
- A successful create is treated as proof that this benchmark run owns the collection.
- If creation fails and the collection exists, the benchmark exits with a refusal
  message instead of running.
- If creation fails for another reason, such as a bad config or unavailable server,
  the original error is re-raised instead of being hidden.
- Cleanup remains safe because it only runs after `claim_collection()` succeeds.

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
