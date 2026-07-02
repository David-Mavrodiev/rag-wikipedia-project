.PHONY: up down ingest eval test lint

up:
	docker compose up --build -d

down:
	docker compose down

ingest:
	PROFILE=$(PROFILE) python backend/pipeline/flow.py

eval:
	python backend/eval/run_eval.py

test:
	cd backend && python -m pytest tests/ -v

lint:
	cd backend && ruff check app/ pipeline/ eval/ tests/
