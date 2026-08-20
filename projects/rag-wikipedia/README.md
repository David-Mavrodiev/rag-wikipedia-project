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

### 2. Pull the LLM (first run only, ~2 GB download)
```bash
make pull-model
```
The Ollama healthcheck only proves the server is up — generation fails with 503 until the model is pulled. Weights persist in the `ollama_data` volume, so this is a one-time step; the embedding model (~130 MB) downloads automatically on first use and is cached in the `hf_cache` volume.

### 3. Ingest Wikipedia (tiny profile ~500 articles)
```bash
PROFILE=tiny make ingest
```

### 4. Create an account
`/query` is closed to anonymous callers. Open http://localhost:5173 and register,
or do it from the shell — the session arrives as a cookie, so keep a jar:

```bash
curl -X POST http://localhost:8000/auth/register -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "username": "alice", "password": "a-good-password"}'
```

Set `JWT_SECRET` in `.env` next to `docker-compose.yml` first; without it the API
signs with a random per-process key and every restart logs everyone out.

### 5. Query
```bash
curl -X POST http://localhost:8000/query -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"question": "Who created Python?"}'
```

Later sessions come from `POST /auth/login` with `{"identifier": "alice",
"password": "…"}` (email or username).

### 6. Run tests
```bash
make test
```

### 7. Run evaluation
```bash
make eval
```
Reports recall@5 and MRR on the answerable golden questions and refusal accuracy on the unanswerable ones (`backend/eval/golden.jsonl`). Exits non-zero if recall@5 or refusal accuracy falls below the 0.8 gate.

## Profiles
| Profile | Articles | Notes |
|---------|----------|-------|
| `tiny`  | ~500     | Fast, for development |
| `real`  | ~25 000  | Full quality pass |

## Swapping models
Set environment variables before starting:
- `EMBED_MODEL` — any SentenceTransformers model (default: `BAAI/bge-small-en-v1.5`)
- `LLM_MODEL` — any Ollama model tag (default: `llama3.2:3b`)

## Authentication
Accounts live in a database (SQLite by default, any SQLAlchemy async URL).
Passwords are hashed with bcrypt, which stores a fresh random salt inside every
digest. Sign-in returns a short-lived HS256 JWT in an **HttpOnly** cookie, so no
page script can read it; `Authorization: Bearer` is still accepted for scripts.

| Endpoint | Purpose |
|----------|---------|
| `POST /auth/register` | email + username + password, signs the new account in |
| `POST /auth/login` | `identifier` (email or username) + password |
| `POST /auth/logout` | clears the session cookie and revokes the session |
| `GET /auth/me` | the signed-in user, or 401 |
| `GET /auth/sessions` | this account's live sessions |
| `DELETE /auth/sessions` | sign out everywhere else |
| `POST /auth/sso/start` | enterprise sign-in: email domain → that tenant's IdP |
| `GET /auth/sso/callback` | the IdP returns here; sets the cookie |
| `GET /auth/oauth/{google\|github}/authorize` | starts the social handshake |
| `GET /auth/oauth/{google\|github}/callback` | provider returns here; sets the cookie |

`GET /health` stays public so container and ingress probes keep working.

### Configuration
| Variable | Meaning |
|----------|---------|
| `DATABASE_URL` | Default `sqlite+aiosqlite:///./data/app.db`; use `postgresql+asyncpg://…` for a server (add `asyncpg`). |
| `JWT_SECRET` | Session signing key, 32+ bytes. Unset ⇒ random per process (dev only; breaks restarts and multiple replicas). |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Sign out an idle browser (default 30). |
| `SESSION_ABSOLUTE_LIFETIME_HOURS` | Ceiling a session can never exceed, however active (default 8). |
| `SECRET_ENCRYPTION_KEY` | Fernet key protecting tenant IdP client secrets at rest. Required before an organization can be registered. |
| `BOOTSTRAP_ADMIN_EMAIL` | The address granted the admin role on sign-in, so the first organization can be created. |
| `COOKIE_SECURE` | Keep `true` in production; `false` only to test over plain http. |
| `FRONTEND_URL` | Where a provider callback sends the browser afterwards. |
| `OAUTH_REDIRECT_BASE_URL` | Public base URL of the API, must match the redirect URI registered with the provider. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth client; unset ⇒ that button returns 503. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth app; unset ⇒ that button returns 503. |

### Sessions
A session is a database row; the cookie only carries its id. That is what makes
`DELETE /auth/sessions`, logout and deactivation take effect on the next request
instead of whenever a token happens to expire. Idle timeout and absolute
lifetime are enforced server-side on every call.

### Social sign-in setup
Register these callback URLs with the providers (the frontend proxies `/auth` to
the API, so the browser-facing origin is the one that must be registered):

```
http://localhost:5173/auth/oauth/google/callback
http://localhost:5173/auth/oauth/github/callback
```

The flow is guarded by a `state` cookie, plus PKCE for Google. GitHub OAuth Apps
ignore PKCE, so `state` alone protects that one.

A provider identity is linked to a local account **only when the provider says
the email is verified** — otherwise anyone able to claim an unverified address
at a provider could take over the matching account. Signing in with Google or
GitHub against an existing email links the two, and the original password keeps
working.

## Enterprise single sign-on
Each organization is a tenant with its own OIDC connection and its own email
domains. Any OIDC provider works — Entra ID, Okta, Auth0, Keycloak — because
endpoints and signing keys come from the issuer's discovery document.

**1. Register the callback URL with the IdP** (one for every tenant):
```
https://<OAUTH_REDIRECT_BASE_URL>/auth/sso/callback
```

**2. Create the organization** as an administrator (`BOOTSTRAP_ADMIN_EMAIL`
grants the first one):
```bash
curl -X POST http://localhost:8000/admin/organizations -b cookies.txt \
  -H "Content-Type: application/json" -d '{
    "name": "Acme",
    "domains": ["acme.com"],
    "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
    "client_id": "<application id>",
    "client_secret": "<client secret>",
    "groups_claim": "groups",
    "role_mappings": {"<group object id>": "admin"}
  }'
```

**3. Users sign in** by entering their work address: the domain routes them to
their tenant's IdP, and the account is created on first success.

| Behaviour | Control |
|-----------|---------|
| Just-in-time provisioning | `allow_jit` (default true); when false, unknown users are refused |
| Password login on managed domains | `enforce_sso` (default true) refuses password login *and* registration for those domains |
| Roles | `role_mappings` maps IdP groups to `member`/`admin`, re-evaluated on **every** sign-in, so removing a group demotes at next login |
| Trusted email | The IdP is believed only for addresses in the domains its organization registered |

The client secret is encrypted with `SECRET_ENCRYPTION_KEY` before it is stored
and is never returned by the API. The handshake uses `state`, a `nonce` and
PKCE; id_tokens are verified against the provider's JWKS with asymmetric
algorithms only, so a token signed with the shared client secret is rejected.

### Administration
Admin-only, gated on the role that came from the IdP:

| Endpoint | Purpose |
|----------|---------|
| `POST /admin/organizations` | register a tenant and its OIDC connection |
| `GET /admin/organizations` | list tenants (never the client secret) |
| `GET /admin/audit-events` | append-only trail of sign-ins, failures, provisioning, revocations |
| `POST /admin/users/{id}/deactivate` | disable an account and drop all of its sessions immediately |

## Architecture
```
Browser → FastAPI → [Embedder → Qdrant → LLM] → cited answer
Prefect pipeline: HF Wikipedia → clean → chunk → embed → Qdrant
```
