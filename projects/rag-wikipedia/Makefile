.PHONY: up down pull-model ingest ingest-force eval eval-groundedness eval-audit test lint docs-check docs-write audit-freshness eval-corpus audit-baseline eval-fixture eval-fixture-all idf idf-fixture sweep-coverage compare-engines

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
	cd backend && uv run ruff check app/ pipeline/ eval/ engines/ tests/ ../scripts

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

# The working collection. Mirrors settings.collection so `make idf` with no
# argument builds the table for the corpus that is actually served.
COLLECTION ?= wikipedia

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

# `eval` and `eval-audit` above follow settings.collection, which defaults to
# the working `wikipedia` collection - correct when you want to score whatever
# you have ingested. But the committed suites describe the FIXTURE corpus, so
# running them straight after `eval-corpus` scored `wikipedia` while the fixture
# sat in `wikipedia_eval`, and reported confident numbers for the wrong corpus.
# These two pin the pair together so that cannot happen.
eval-fixture:
	PROFILE=fixture COLLECTION=$(EVAL_COLLECTION) uv run python backend/eval/run_eval.py

# Ingest the fixture and score it in one step - the safe default for anyone who
# just wants to see the eval run.
eval-fixture-all: eval-corpus eval-fixture


# --- IDF evidence tables -----------------------------------------------------
# The document-frequency table `decide_evidence` weights evidence with. Built
# from the INDEXED chunks, not from a corpus file, so it always describes the
# collection actually being served.
#
# Rebuild after ANY ingest that changes a collection's contents. A stale table
# does not error - it silently scores terms with the wrong corpus's
# frequencies, which is the one failure here that produces plausible numbers.
idf:
	cd backend && uv run python ../scripts/build_idf.py --collection $(COLLECTION)

idf-fixture:
	cd backend && uv run python ../scripts/build_idf.py --collection $(EVAL_COLLECTION)

# Score every registered engine on the SAME suites and report the delta.
# PROFILE and COLLECTION are pinned for exactly the reason eval-fixture pins
# them: the committed suites describe the fixture corpus, and scoring an engine
# against a different one would compare it to a baseline it never ran on.
#
# Needs Ollama. Generation dominates - measured at ~61 s per answered case on
# this machine - so the full three suites are hours, not minutes. Pass ARGS to
# narrow it, e.g. ARGS="--suite holdout --suite adversarial".
compare-engines:
	cd backend && PROFILE=fixture COLLECTION=$(EVAL_COLLECTION) uv run python eval/compare_engines.py $(ARGS)

# Measure refusal_min_evidence_coverage and choose a threshold. Needs `idf`
# first. The number to beat is false_accept_rate 0.600 on the serving corpus.
sweep-coverage:
	cd backend && uv run python ../scripts/sweep_coverage.py --collection $(COLLECTION) --suite $(SUITE)

SUITE ?= serving_golden.jsonl
