# Runbook — RAG Wikipedia

## Services

| Service  | Port  | Health endpoint |
|----------|-------|----------------|
| api      | 8000  | GET /health    |
| frontend | 5173  | HTTP 200       |
| qdrant   | 6333  | GET /healthz   |
| ollama   | 11434 | GET /          |

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

**`401 Not authenticated` / `401 Invalid token`** — `/query` needs a session. Register or sign in (`POST /auth/register`, `POST /auth/login`), which sets the `rag_session` cookie. If sessions drop after every restart, `JWT_SECRET` is unset and the API is signing with a random per-process key. If the browser never keeps the cookie, check `COOKIE_SECURE`: a Secure cookie is discarded over plain http.

**Social sign-in redirects back with `?auth_error=`** — `oauth_failed` means the handshake failed (wrong client id/secret, a redirect URI that does not match `OAUTH_REDIRECT_BASE_URL`, or a stale tab whose `state` cookie expired). `email_unverified` means the provider does not vouch for that address, which is refused on purpose.

**`403 This domain uses single sign-on`** — expected: that domain belongs to an organization with `enforce_sso`, so passwords are refused for it. Sign in through SSO, or register the tenant with `enforce_sso: false`.

**Enterprise sign-in redirects back with `?auth_error=`** — `sso_failed` covers discovery, token exchange and id_token verification (check the issuer, client id/secret and that the IdP's redirect URI is `<OAUTH_REDIRECT_BASE_URL>/auth/sso/callback`). `sso_domain_mismatch` means the IdP returned an address outside the domains that organization registered. `sso_no_account` means `allow_jit` is off and nobody has provisioned the user. The API log carries the underlying reason, and every one of these is in `GET /admin/audit-events`.

**`503 SECRET_ENCRYPTION_KEY is not configured`** — tenant client secrets are encrypted at rest, so registering an organization needs that key. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Replacing an existing key makes previously stored secrets undecryptable (503 at sign-in) — re-register the connections.

**Users are signed out sooner than expected** — check `SESSION_IDLE_TIMEOUT_MINUTES` (default 30) and `SESSION_ABSOLUTE_LIFETIME_HOURS` (default 8). Both are enforced server-side; the absolute one is never extended by activity.

**`503 Vector store unavailable`** — Check Qdrant is healthy: `curl http://localhost:6333/healthz`

**`503 LLM unavailable`** — Check Ollama: `curl http://localhost:11434/` and `docker compose exec ollama ollama list`. If `ollama list` shows no models, the weights were never pulled (the healthcheck only proves the server is up) — run `make pull-model`.

**Slow ingestion** — Normal for real profile; use `PROFILE=tiny` for development.

**Empty answers** — Run evaluation to check recall scores. May need re-ingestion.
