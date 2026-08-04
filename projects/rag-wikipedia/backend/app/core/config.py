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


settings = Settings()
