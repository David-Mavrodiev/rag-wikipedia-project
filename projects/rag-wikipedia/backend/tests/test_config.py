def test_defaults():
    from app.core.config import Settings

    settings = Settings()
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.top_k == 5
    assert settings.profile == "tiny"
    assert settings.refusal_min_score == 0.45
    assert settings.retrieval_candidate_k == 20


def test_env_override(monkeypatch):
    monkeypatch.setenv("TOP_K", "10")
    monkeypatch.setenv("PROFILE", "real")

    from app.core.config import Settings

    settings = Settings()
    assert settings.top_k == 10
    assert settings.profile == "real"
