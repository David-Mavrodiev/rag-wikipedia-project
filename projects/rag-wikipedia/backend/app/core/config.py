from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://localhost:6333"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    llm_model: str = "llama3.2:3b"
    collection: str = "wikipedia"
    top_k: int = 5
    profile: str = "tiny"
    token_budget: int = 3000
    refusal_threshold: float = 0.3
    allowed_origins: str = "*"

    # --- auth ---------------------------------------------------------------
    # Any SQLAlchemy async URL. SQLite by default so the stack runs with no
    # extra service; point it at postgresql+asyncpg://... in production.
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    # HS256 signing key, 32+ bytes. When empty a random key is generated per
    # process: usable for local dev, but sessions die on restart and are not
    # shared across replicas, so set this explicitly anywhere that matters.
    jwt_secret: str = ""

    # Sessions are rows in the database; the cookie only carries their id. Idle
    # expiry logs out an abandoned browser, and the absolute lifetime caps how
    # long a stolen cookie is worth anything even if it is used constantly.
    session_idle_timeout_minutes: int = 30
    session_absolute_lifetime_hours: int = 8

    # The JWT travels in an HttpOnly cookie so page scripts can never read it.
    session_cookie_name: str = "rag_session"
    # Must stay True in production; only turn it off to test over plain http.
    cookie_secure: bool = True
    cookie_domain: str = ""

    # Where the OAuth/OIDC callback sends the browser once the cookie is set,
    # and the public base URL of THIS api (must match the redirect URI
    # registered with the provider). Never taken from the request, so it cannot
    # be used as an open redirect.
    frontend_url: str = "http://localhost:5173"
    oauth_redirect_base_url: str = "http://localhost:8000"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    # --- enterprise ---------------------------------------------------------
    # Fernet key protecting each organization's IdP client secret at rest.
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_encryption_key: str = ""
    # The single account that is granted the admin role, so somebody can
    # register the first organization. Applied whenever that address signs in.
    bootstrap_admin_email: str = ""


settings = Settings()
