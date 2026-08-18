# CODEX RUNBOOK — environment, the `codex_apps` fix, and troubleshooting

Everything needed to keep the **Codex CLI** healthy on this machine, plus the
diagnosis of the recurring startup warning and how to reverse the fix.

Companion to [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) (which covers running the RAG stack).
Relevant because Stage 2 of the nonplusultra process is a **Codex Lab Assessment** —
see [MASTER_TRAINER_PREP_PLAN.md](MASTER_TRAINER_PREP_PLAN.md).

---

## 1. The problem

Every single launch of `codex` printed:

```text
⚠ MCP startup interrupted. The following servers were not initialized:
  codex_apps
```

Systematic — it appeared right after the `OpenAI Codex (v0.147.0)` banner, on every
run, in the VS Code integrated terminal.

## 2. Root cause (measured, not guessed)

`codex_apps` is **not** the local plugin host. It is the **ChatGPT Apps connector
layer**, and it was enumerating **248 tools on every startup**:

| Namespace | Tools |
|---|---|
| `codex_apps__github` | 89 |
| `codex_apps__google_drive` | 45 |
| `codex_apps__sites` | 33 |
| `codex_apps__slack` | 32 |
| `codex_apps__gmail` | 21 |
| `codex_apps__google_calendar` | 15 |
| others (safety, plugin mgmt, doc control, hotline) | 13 |

Plus cached payloads under `~/.codex/cache/`:

```text
codex_apps_tools        2.3 MB
codex_app_directory     1.6 MB
remote_plugin_catalog    12 MB
```

On a loaded 16 GB machine that consistently exceeded the TUI's startup budget, so
startup was cut short — *"interrupted"* — with `codex_apps` reported as not
initialized. Codex itself still worked; the warning was the visible symptom.

**Why an earlier attempt failed:** disabling the *local* `gmail` / `slack` /
`google-drive` plugins in `config.toml` changed nothing, because those 248 tools come
from the **remote apps directory**, not from local plugins. The payload never shrank.
Measure the payload before assuming the cause.

## 3. The fix

```powershell
codex features disable apps
```

Writes to `~/.codex/config.toml`:

```toml
[features]
js_repl = false
apps = false          # codex_apps is never spawned -> nothing left to fail
```

Stale caches were also cleared (they rebuild on demand):

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.codex\cache\codex_apps_tools",
                            "$env:USERPROFILE\.codex\cache\codex_apps_server_info",
                            "$env:USERPROFILE\.codex\cache\codex_app_directory"
```

### Result

| Metric | Before | After |
|---|---|---|
| `codex doctor` | 16 ok · 1 warn | **17 ok · 0 warn · 0 fail** |
| Startup (`codex exec` round trip) | 43 s | **6 s** |
| `codex_apps` warning | every launch | **gone** |

### What this costs
ChatGPT Apps inside Codex — Sites, and the Gmail / Slack / Drive / Calendar / GitHub
**connectors**. Codex's core (reading the repo, planning, editing files, running
commands) is untouched. For a coding assessment this is a net win: 248 fewer tools in
the model's context.

## 4. Turning apps back on

```powershell
codex features enable apps     # re-enables codex_apps + the connectors
codex features list | Select-String "^apps"   # verify: apps  stable  true
```

Caches rebuild automatically on the next launch (expect a slower first start — and
the `MCP startup interrupted` warning may return, since that is the trade-off).

If you re-enable apps and also want the local connector plugins back, flip these in
`~/.codex/config.toml` (they were set to `false` during diagnosis):

```toml
[plugins."google-drive@openai-curated"]
enabled = true
[plugins."gmail@openai-curated"]
enabled = true
[plugins."slack@openai-curated"]
enabled = true
```

**To disable again:** `codex features disable apps`.

## 5. Second fix applied — the stale `node_repl` MCP server

`~/.codex/config.toml` contained an `[mcp_servers.node_repl]` block pointing at
**three binaries inside an obsolete Codex install** (`AppData\Local\OpenAI\Codex\bin\…`,
June 6), including a stale `codex.exe` **0.137.0-alpha.4**. It was spawned at every
startup to serve `js_repl`, a feature already **disabled** — and superseded by
`codex-code-mode-host.exe` in 0.147.0.

Removed with Codex's own command (not by hand-editing TOML):

```powershell
codex mcp remove node_repl
```

That alone cut startup 43 s → 10 s and eliminated two errors:
`unknown variant 'max'` (0.137 client vs current server schema) and
`timeout waiting for child process to exit`.

## 6. Multiple Codex installs on this machine

| Path | Version | Notes |
|---|---|---|
| `AppData\Local\Programs\OpenAI\Codex\bin\codex.exe` | **0.147.0** | ✅ what `codex` resolves to (User PATH position 1) |
| `AppData\Local\OpenAI\Codex\bin\fb2111…\codex.exe` | 0.137.0-alpha.4 | obsolete; was referenced by `node_repl` |
| `AppData\Roaming\npm\codex.cmd` | 0.137.0 | npm copy, PATH position 13 — harmless but a future foot-gun |

Optional cleanup: `npm uninstall -g @openai/codex`.

`codex update` reports *"Could not detect the Codex installation method"* — this build
is desktop-app managed. That is expected; you are already on the latest.

> The old binary's version comparison reported itself as "not older" than 0.147.0, so
> it never self-updated. Version skew here is silent — check `codex --version` against
> `~/.codex/version.json` (`latest_version`) rather than trusting the update prompt.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MCP startup interrupted … codex_apps` | Apps connector layer loading 248 tools at startup | `codex features disable apps` (§3) |
| `unknown variant 'max', expected none/minimal/low/medium/high/xhigh` | Stale client (0.137) vs current server schema | Use the 0.147.0 binary on PATH; remove stale MCP entries pointing at old installs |
| `timeout waiting for child process to exit` | MCP server binary from an obsolete install | `codex mcp remove <name>` |
| Plugin/marketplace doubts | — | `codex plugin list`, `codex mcp list`, `codex doctor` |
| `search command could not be verified` (doctor warn) | ripgrep not installed | Cosmetic; install ripgrep for faster in-Codex search |

**First move for any Codex issue:** `codex doctor` — it checks install, config, auth,
sandbox, connectivity, and MCP in one pass.

## 8. Backups

Config snapshots taken during this work, in `~/.codex/`:

```text
config.toml.bak-20260818-121941   before removing node_repl
config.toml.bak-plugins-*         before disabling the 3 connector plugins
config.toml.bak-apps-*            before disabling the apps feature
```

Restore with `Copy-Item <backup> $env:USERPROFILE\.codex\config.toml`.

---

## 9. Use this as an interview story

This maps directly onto David's criterion 4 — *troubleshoot calmly when the code,
tests, or environment fail* — and it is a stronger story than a code bug because it
shows **method**:

1. **Reproduced** it precisely (every launch, right after the banner).
2. **Rejected the first hypothesis with evidence** — disabling the local plugins
   changed nothing, which *disproved* the "local plugins are slow" theory.
3. **Measured instead of guessing** — counted the tools (248) and the cached payload
   (16 MB total). That number *was* the diagnosis.
4. **Fixed at the source** with the tool's own command, not a hand-edited config.
5. **Verified**: doctor 0 warn, startup 43 s → 6 s.
6. **Made it reversible** and wrote it down (this file).

> The line: *"I stopped guessing and measured what it was actually loading. 248 tools
> at every startup — that was the answer, and the fix was one flag."*
