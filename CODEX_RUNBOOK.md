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

Measured with (Docker/Codex idle, nothing else running):

```powershell
# tool count and namespace breakdown
python -c "import json,glob; d=json.load(open(glob.glob('$env:USERPROFILE/.codex/cache/codex_apps_tools/*.json')[0],encoding='utf-8')); print(len(d['tools']))"
# cached payload sizes
du -sh ~/.codex/cache/codex_apps_tools ~/.codex/cache/codex_app_directory ~/.codex/cache/remote_plugin_catalog
```

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
js_repl = false       # was ALREADY false - not changed by this fix
apps = false          # <- the only flag this fix sets
```

Only `apps` was changed. `js_repl` was already disabled before any of this work
began (which is what made the stale `node_repl` server in §5 pure dead weight).

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
commands) is untouched. For a coding assessment this is a net win: those 248 tool
definitions are no longer loaded at startup.

> Whether every one of them would have entered the model's context on each turn was
> **not measured** — tool exposure can be filtered per request. The verified claims
> are the startup cost (43 s → 6 s) and the removal of the warning; treat any
> context-window saving as plausible but unquantified.

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
is desktop-app managed, so the CLI cannot update itself in place. That is expected.

**That failure says nothing about versions.** To check whether you are current,
compare the two sources of truth directly:

```powershell
codex --version                                   # installed: 0.147.0
Get-Content "$env:USERPROFILE\.codex\version.json"  # latest_version: 0.147.0
```

At the time of writing those matched, so no update was needed — but re-check rather
than inferring it from the update command failing.

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
| `search command could not be verified` (doctor warn) | ripgrep genuinely absent (`rg` is not on PATH on this machine) | Install ripgrep. **Codex's fallback behaviour was not verified here**, so do not assume the warning is harmless — it may mean slower or less complete in-repo search |

**First move for any Codex issue:** `codex doctor` — it checks install, config, auth,
sandbox, connectivity, and MCP in one pass.

## 8. Backups

Config snapshots taken during this work, in `~/.codex/`:

```text
config.toml.bak-20260818-121941   before removing node_repl
config.toml.bak-plugins-*         before disabling the 3 connector plugins
config.toml.bak-apps-*            before disabling the apps feature
```

Restore one of them — **preserving the current file first**, so a bad restore is
itself reversible:

```powershell
$codex = "$env:USERPROFILE\.codex"
Get-ChildItem "$codex\config.toml.bak-*" | Sort-Object LastWriteTime   # list backups

# back up what is there NOW, then restore the chosen snapshot
Copy-Item "$codex\config.toml" "$codex\config.toml.bak-before-restore-$(Get-Date -f yyyyMMdd-HHmmss)"
Copy-Item "$codex\config.toml.bak-20260818-121941" "$codex\config.toml" -Force

codex doctor          # confirm the restored config still parses
```

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
> at every startup — that was the answer."*

Be precise if asked what the fix was: **two** changes landed, not one. Removing the
stale `node_repl` server (§5) cut startup 43 s → 10 s and cleared two errors;
disabling `apps` removed the warning itself and took startup to 6 s. The one-flag
line is the headline, not the whole story.
