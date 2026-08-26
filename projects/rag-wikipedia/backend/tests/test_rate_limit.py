from __future__ import annotations

from unittest.mock import patch

import pytest
from app.core import rate_limit
from app.core.config import settings


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.loaded = False
        self.remaining = 1

    async def time(self):
        if self.fail:
            raise rate_limit.RedisError("redis unavailable")
        return (1, 0)

    async def script_load(self, script):
        if self.fail:
            raise rate_limit.RedisError("redis unavailable")
        self.loaded = True
        return "sha"

    async def evalsha(self, sha, keys, key, *args):
        if self.fail:
            raise rate_limit.RedisError("redis unavailable")
        if self.remaining <= 0:
            return [0, 0, 60_000, 120_000]
        self.remaining -= 1
        return [1, self.remaining, 0, 60_000]


@pytest.fixture
def rate_limited_client(client, monkeypatch):
    fake_redis = FakeRedis()
    # monkeypatch restores these after the test; direct assignment leaked a
    # 1-request-per-minute limit into every test that ran afterwards.
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_query_per_minute", 1)
    monkeypatch.setattr(settings, "rate_limit_query_burst", 1)
    monkeypatch.setattr(settings, "rate_limit_client_header", "")
    monkeypatch.setattr(rate_limit, "_redis_client", fake_redis)
    monkeypatch.setattr(rate_limit, "_rate_limit_script_sha", None)
    return client


def test_query_returns_429_after_limit(rate_limited_client):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = [
            {
                "score": 0.95,
                "text": "Python is a programming language.",
                "title": "Python",
                "source_id": "1",
            }
        ]
        mock_llm.return_value.generate.return_value = "Python [1] is a language."

        first = rate_limited_client.post("/query", json={"question": "What is Python?"})
        second = rate_limited_client.post("/query", json={"question": "What is Python?"})

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"
    assert second.status_code == 429
    assert second.json() == {"detail": "Rate limit exceeded"}
    assert second.headers["Retry-After"] == "60"
    assert second.headers["X-RateLimit-Remaining"] == "0"


def test_rate_limit_blocks_before_expensive_query_work(rate_limited_client):
    rate_limit._redis_client.remaining = 0

    with patch("app.api.query._embedder") as mock_embedder:
        response = rate_limited_client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 429
    mock_embedder.assert_not_called()


def test_health_is_not_rate_limited(rate_limited_client):
    rate_limit._redis_client.remaining = 0

    response = rate_limited_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_fails_closed_when_redis_is_unavailable(client, monkeypatch):
    settings.rate_limit_enabled = True
    monkeypatch.setattr(rate_limit, "_redis_client", FakeRedis(fail=True))
    monkeypatch.setattr(rate_limit, "_rate_limit_script_sha", None)

    response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Rate limiter unavailable"}
