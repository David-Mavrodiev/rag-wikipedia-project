.PHONY: up down pull-model ingest eval test lint

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
ingest:
	PROFILE=$(PROFILE) uv run python backend/pipeline/flow.py

eval:
	uv run python backend/eval/run_eval.py

test:
	cd backend && uv run pytest tests/ -v

lint:
	cd backend && uv run ruff check app/ pipeline/ eval/ tests/
