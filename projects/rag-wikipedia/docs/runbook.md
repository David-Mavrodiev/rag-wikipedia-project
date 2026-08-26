# Runbook — RAG Wikipedia

## Services

| Service  | Port                | Health endpoint |
|----------|---------------------|----------------|
| api      | 8000                | GET /health    |
| frontend | 5173                | HTTP 200       |
| qdrant   | 6333                | GET /healthz   |
| ollama   | 11434               | GET /          |
| redis    | 127.0.0.1:6379      | `redis-cli ping` |

**Redis is not optional.** Rate limiting is enabled by default and
`POST /query` answers **503** when the limiter cannot reach it. Set
`RATE_LIMIT_ENABLED=false` for a single-machine run without Redis. It is bound
to loopback deliberately: this Redis has no password.

## API surface

| Method | Path             | Notes |
|--------|------------------|-------|
| GET    | `/health`        | liveness; never rate limited |
| POST   | `/query`         | rate limited; 429 over budget, 503 if Redis is unreachable |
| GET    | `/quality`       | last audit verdict and metrics; public |
| POST   | `/quality/audit` | re-scores every suite. 409 if one is already running. `?auto_correct=true` rewrites and persists the refusal thresholds and requires `X-Quality-Token`. Refused at the nginx edge in deployed stacks |

## Common tasks

### Check service status
```bash
docker compose ps
docker compose logs api --tail=50
```

### Re-ingest from scratch
```bash
docker compose exec qdrant sh -c "rm -rf /qdrant/storage/*"
docker compose restart qdrant
PROFILE=tiny make ingest
```

### Switch to a different LLM
```bash
docker compose exec ollama ollama pull mistral:7b
LLM_MODEL=mistral:7b docker compose up api -d
```

### Switch embedding model
```bash
EMBED_MODEL=BAAI/bge-base-en-v1.5 docker compose up api -d
# Note: different dim (768) requires dropping and recreating the Qdrant collection
```

### Troubleshooting

**`503 Vector store unavailable`** — Check Qdrant is healthy: `curl http://localhost:6333/healthz`

**`503 LLM unavailable`** — Check Ollama: `curl http://localhost:11434/` and `docker compose exec ollama ollama list`. If `ollama list` shows no models, the weights were never pulled (the healthcheck only proves the server is up) — run `make pull-model`.

**Slow ingestion** — Normal for real profile; use `PROFILE=tiny` for development.

**Empty answers** — Run evaluation to check recall scores. May need re-ingestion.
