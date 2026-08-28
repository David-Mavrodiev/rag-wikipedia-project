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

### Resume an interrupted ingest

This is almost always what you want. Just run it again:

```bash
PROFILE=real make ingest
```

Ingestion is resumable: point IDs are `sha256(source_id::chunk_index)`, and each
segment asks Qdrant which IDs it already holds and embeds only what is missing.
A re-run costs one lookup per segment, not the whole embedding bill. The 25,000
article run survived four machine shutdowns this way without re-embedding a
single chunk.

### Re-ingest from scratch

**Destructive, and rarely necessary.** This deletes every vector in every
collection; re-embedding 25k articles is hours of GPU time. Resuming (above)
handles interruptions, and `--force` handles changed embeddings, so reach for
this only when the storage itself is corrupt.

```bash
docker compose exec qdrant sh -c "rm -rf /qdrant/storage/*"
docker compose restart qdrant
make eval-corpus            # the 150-article fixture the eval suites target
PROFILE=tiny make ingest    # or: a 500-article dev index
```

### Re-embed without wiping

When the embedding model or the chunking parameters change, the point IDs stay
the same while the vectors they should hold do not. Overwrite in place instead
of deleting:

```bash
cd backend && uv run python -m pipeline.flow --profile real --force
```

### Re-measure quality after a change

```bash
make audit-baseline     # ingested fixture in, audit_report.json out
make audit-freshness    # fails if the report predates a change that can move it
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

**Slow ingestion** — Normal for the real profile. On a thermally constrained
machine it is often *thermal*, not algorithmic: check the GPU clock
(`nvidia-smi --query-gpu=clocks.sm,temperature.gpu --format=csv`) before changing
any code. A card clamped to 210 MHz of 2,100 is a cooling problem that no code
change recovers. Use `PROFILE=tiny` for development.

**The machine shut down mid-ingest** — Thermal protection, not a fault, and not
Docker crashing (Windows logs Event ID 41). Nothing is lost: restart the
services and re-run the same ingest command, which resumes. The full procedure
is in [PERTINENT.md](../../../PERTINENT.md).

**Empty answers** — Run evaluation to check recall scores. May need re-ingestion.
