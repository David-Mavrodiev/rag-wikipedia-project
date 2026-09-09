# CLAUDE.md — rag-wikipedia-project

Local, framework-free RAG over Wikipedia: clean → chunk → `bge-small-en-v1.5` →
Qdrant → `llama3.2:3b`, served by FastAPI behind a React/Vite UI, with Prefect
running the ingestion flow. There is no RAG framework here on purpose — every
step in the chain is explicit and unit-tested. Keep it that way.

## Layout

`projects/rag-wikipedia/` is the system; paths below are relative to it.

- `backend/app/` — FastAPI: `api/` routes, `core/` the chain (chunking,
  embeddings, retrieval, prompt, refusal, citations, idf, quality)
- `backend/pipeline/` — Prefect ingestion flow
- `backend/eval/` — suites (`*.jsonl`), `run_eval.py`, `audit.py`, reports
- `backend/tests/` — pytest. `frontend/src/` — React + Vitest
- `scripts/` — `docs_check`, `audit_freshness`, `build_idf`, `sweep_coverage`

`PROJECT_BLUEPRINT.md`, `ANNOTATED_CODE.md` and `TESTS_ANNOTATED.md` at the repo
root are tens of thousands of tokens each. **Never read them end to end** — grep
for the section, or delegate the reading. See `AI_CODING_WORKFLOW.md`.

## Commands (from `projects/rag-wikipedia/`)

```bash
make test               # pytest, backend
make lint               # ruff
make eval-fixture-all   # ingest the committed fixture, then score it
make docs-check         # assert documented facts against the repository
cd frontend && npm test # vitest
```

Python 3.12, uv workspace (`.venv` here, `backend` is a member) — always
`uv run`, never a bare `python`. ruff: line-length 100, `E,F,I,UP`. Frontend: npm.

## Invariants — break these and the numbers start lying

- **The red gates are red on purpose.** `false_accept_rate` is 0.600 against a
  0.10 gate and the eval exits non-zero. Never relax a threshold to make it
  pass; fix the refusal logic, or record why it can't be fixed.
- **A collection belongs to a profile.** The committed suites describe the
  fixture corpus in `wikipedia_eval`; the served corpus is `wikipedia`. Never
  score one against the other — use the paired `make` targets.
- **Rebuild the IDF table after any ingest** (`make idf`). A stale table does
  not error; it silently scores terms with the wrong corpus's frequencies.
- **`audit_report.json` carries provenance** (git sha, profile, collection,
  vector count). Regenerate it, never hand-edit it; `make audit-freshness`
  fails when it goes stale.
- Documents marked **point-in-time record** are dated evidence. Where they
  disagree with the repository, the repository is right — write the correction
  above the claim it replaces rather than rewriting the claim.

## Working style

- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`), kept small.
  Current branch is `hadi-dev`.
- `make test` and `make lint` before calling anything done — plus `make
  docs-check` if you touched a documented fact, count, or setting.
- Problem → fix notes go in `PERTINENT.md`; debugging sessions in
  `docs/debug-log/`.

## Adding tests or settings — what `docs-check` will ask for

`make docs-check` enforces documented facts two ways, and only one is automatic.

- **Generated blocks** are rewritten by `make docs-write`: `test-inventory`
  (`TESTS_ANNOTATED.md`, `TEST_COVERAGE_SUMMARY.md`), `settings`
  (`ANNOTATED_CODE.md`), `services` (`PROJECT_BLUEPRINT.md`), `eval-suites`
  (`projects/rag-wikipedia/README.md`).
- **Asserted claims are corrected BY HAND.** That is deliberate — the script
  says so when it fails, because these numbers are meant to stay inline and
  readable. Measured: adding one test file holding two tests fails six claims
  across five files — `README.md`, `TESTS_ANNOTATED.md` (twice: the test count
  and the file count), `PROJECT_BLUEPRINT.md`,
  `MASTER_TRAINER_LESSON_SCRIPT.md`, `MASTER_TRAINER_PREP_PLAN.md`.

The failure prints `file:line`, the documented number, the repository's number
and the matched text, so each edit is mechanical. Make them in the SAME commit
that moves the count: a commit that leaves `docs-check` red is one CI rejects.

Documents that declare themselves a `point-in-time record` are exempt and must
NOT be updated to match — `ENGINEERING_QUALITY_ASSESSMENT.md` still reports the
count that was true when it was written, which is the convention working.

Adding a field to `Settings` also regenerates the `settings` block — and
because that field lives in `config.py`, which `audit_freshness.py` watches, the
same change stales `audit_report.json`. See the invariants above.
