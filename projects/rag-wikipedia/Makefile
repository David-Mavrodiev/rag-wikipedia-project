.PHONY: up down pull-model ingest ingest-force eval eval-groundedness eval-audit test lint docs-check docs-write audit-freshness eval-corpus audit-baseline

up:
	docker compose up --build -d

pull-model:
	docker compose exec ollama ollama pull llama3.2:3b

down:
	docker compose down

# These run `uv run` from here on purpose: pyproject.toml in this directory
# declares a uv workspace with `backend` as a member, so uv resolves the shared
# workspace environment (.venv here) from either location. Verified: running
# from here and from backend/ both resolve the same interpreter.
# Resumable: chunks already in Qdrant are skipped, so an interrupted run can be
# re-issued and costs one cheap ID lookup per article instead of re-embedding.
ingest:
	PROFILE=$(PROFILE) uv run python backend/pipeline/flow.py

# Re-embed and overwrite everything. Needed when EMBED_MODEL or the chunk size
# changes: the deterministic point IDs stay the same while the vectors they
# should hold do not, so a normal run would keep the stale ones.
ingest-force:
	PROFILE=$(PROFILE) uv run python backend/pipeline/flow.py --force

# Fast path: retrieval-only, seconds, no LLM needed.
eval:
	uv run python backend/eval/run_eval.py

# Opt-in: also measures groundedness -> one LLM call per answerable case,
# so this needs Ollama running and takes minutes instead of seconds.
eval-groundedness:
	uv run python backend/eval/run_eval.py --with-groundedness

eval-audit:
	uv run python backend/eval/audit.py

test:
	cd backend && uv run pytest tests/ -v

lint:
	cd backend && uv run ruff check app/ pipeline/ eval/ tests/

# Verify the documentation's factual claims against the repository. Generated
# blocks are rewritten from source; inline claims are asserted.
docs-check:
	uv run python scripts/docs_check.py

docs-write:
	uv run python scripts/docs_check.py --write

# Fail if audit_report.json predates a change that could invalidate its numbers.
# Needs full git history: a shallow clone cannot resolve commit ancestry.
audit-freshness:
	uv run python scripts/audit_freshness.py

# The collection holding the committed evaluation corpus. Separate from the
# working `wikipedia` collection on purpose: the published measurement must
# describe a fixed corpus, not whatever a local ingest happens to contain.
EVAL_COLLECTION ?= wikipedia_eval

# Ingest the committed fixture. No network, no Hugging Face - deterministic.
# Resumable, so re-running costs an ID lookup per article rather than re-embedding.
eval-corpus:
	PROFILE=fixture COLLECTION=$(EVAL_COLLECTION) uv run python backend/pipeline/flow.py

# The canonical audit: fixed corpus in, audit_report.json out. This is exactly
# what CI runs, so a local result and a CI result are comparable.
# PROFILE must match the ingest: provenance records settings.profile, so
# auditing with a different one would label the fixture corpus as something
# else - the precise inaccuracy provenance exists to prevent.
audit-baseline:
	PROFILE=fixture COLLECTION=$(EVAL_COLLECTION) uv run python backend/eval/audit.py
