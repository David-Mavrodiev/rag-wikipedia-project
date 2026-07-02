# RAG Wikipedia

End-to-end Retrieval-Augmented Generation over Wikipedia, powered by:
- **Qdrant** (vector search)
- **Ollama + llama3.2:3b** (generation)
- **bge-small-en-v1.5** (embeddings)
- **FastAPI** (backend)
- **React + Vite** (frontend)

## Quickstart

### Prerequisites
- Docker + Docker Compose
- 8 GB RAM (for model + embeddings)

### 1. Start services
```bash
docker compose up --build -d
```

### 2. Pull the LLM (first run only)
```bash
docker compose exec ollama ollama pull llama3.2:3b
```

### 3. Ingest Wikipedia (tiny profile ~500 articles)
```bash
PROFILE=tiny make ingest
```

### 4. Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Who created Python?"}'
```

Or open http://localhost:5173 in your browser.

### 5. Run tests
```bash
make test
```

### 6. Run evaluation
```bash
make eval
```

## Profiles
| Profile | Articles | Notes |
|---------|----------|-------|
| `tiny`  | ~500     | Fast, for development |
| `real`  | ~25 000  | Full quality pass |

## Swapping models
Set environment variables before starting:
- `EMBED_MODEL` — any SentenceTransformers model (default: `BAAI/bge-small-en-v1.5`)
- `LLM_MODEL` — any Ollama model tag (default: `llama3.2:3b`)

## Architecture
```
Browser → FastAPI → [Embedder → Qdrant → LLM] → cited answer
Prefect pipeline: HF Wikipedia → clean → chunk → embed → Qdrant
```
