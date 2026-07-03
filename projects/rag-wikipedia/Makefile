.PHONY: up down pull-model ingest eval test lint

up:
	docker compose up --build -d

pull-model:
	docker compose exec ollama ollama pull llama3.2:3b

down:
	docker compose down

ingest:
	PROFILE=$(PROFILE) uv run python backend/pipeline/flow.py

eval:
	uv run python backend/eval/run_eval.py

test:
	cd backend && uv run pytest tests/ -v

lint:
	cd backend && uv run ruff check app/ pipeline/ eval/ tests/
