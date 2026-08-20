from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://localhost:6333"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    llm_model: str = "llama3.2:3b"
    collection: str = "wikipedia"

    # Context window requested from the LLM. Ollama sizes its CUDA compute
    # buffers from this, and llama3.2's advertised maximum overflows a 6 GB
    # card, so it is pinned rather than inherited from the server's default.
    # Must exceed token_budget plus prompt and answer headroom.
    llm_num_ctx: int = Field(default=8192, ge=512, le=131072)
    top_k: int = Field(default=5, ge=1, le=100)
    profile: str = "tiny"
    token_budget: int = Field(default=3000, ge=1)

    # Retrieval controls. These same five are also settable at RUNTIME through
    # /quality/audit?auto_correct=true, so runtime_config re-checks identical
    # bounds on load: a value rejected here must not become acceptable just
    # because it arrived through runtime_config.json instead of the environment.
    refusal_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    refusal_min_score: float = Field(default=0.45, ge=0.0, le=1.0)
    refusal_high_confidence_score: float = Field(default=0.78, ge=0.0, le=1.0)
    refusal_min_margin: float = Field(default=0.02, ge=0.0, le=1.0)
    refusal_min_overlap_terms: int = Field(default=1, ge=0, le=50)
    retrieval_candidate_k: int = Field(default=20, ge=1, le=1000)

    allowed_origins: str = "*"
    rate_limit_enabled: bool = True
    rate_limit_redis_url: str = "redis://localhost:6379/0"
    rate_limit_query_per_minute: int = Field(default=10, ge=1)
    rate_limit_query_burst: int = Field(default=20, ge=1)
    rate_limit_client_header: str = ""

    # Required only for the auto-correcting audit, which PERSISTS new retrieval
    # thresholds. Empty means that path is disabled rather than open.
    quality_admin_token: str = ""


settings = Settings()
