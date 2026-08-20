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
    refusal_min_score: float = 0.45
    refusal_high_confidence_score: float = 0.78
    refusal_min_margin: float = 0.02
    refusal_min_overlap_terms: int = 1
    retrieval_candidate_k: int = 20
    allowed_origins: str = "*"
    rate_limit_enabled: bool = True
    rate_limit_redis_url: str = "redis://localhost:6379/0"
    rate_limit_query_per_minute: int = 10
    rate_limit_query_burst: int = 20
    rate_limit_client_header: str = ""


settings = Settings()
