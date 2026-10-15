"""Startup warm-up: the first user must not pay for loading the models.

Measured before this existed: 75.4 s for the first answer after a start (60.8 s
embedder, 13.2 s LLM) against 2.9-4.9 s warm. These cover what makes warming up
safe to do unattended: each dependency is exercised, a failure in one never
stops the others or the API, and it can be switched off.
"""

from __future__ import annotations

from unittest.mock import patch

from app.api import query
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


def _patched():
    return (
        patch("app.api.query._embedder"),
        patch("app.api.query._store"),
        patch("app.api.query._llm"),
    )


def test_warm_up_exercises_every_dependency_once():
    e, s, l_ = _patched()
    with e as embedder, s as store, l_ as llm:
        timings = query.warm_up()

    embedder.return_value.embed.assert_called_once()
    store.assert_called_once()
    llm.return_value.generate.assert_called_once()
    assert set(timings) == {"embedder", "store", "llm"}


def test_an_llm_that_is_not_up_yet_does_not_keep_the_embedder_cold():
    e, s, l_ = _patched()
    with e as embedder, s, l_ as llm:
        llm.return_value.generate.side_effect = ConnectionError("ollama starting")
        timings = query.warm_up()  # must not raise

    embedder.return_value.embed.assert_called_once()
    assert "llm" not in timings and "embedder" in timings


def test_startup_warms_up_in_the_background_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "warmup_on_startup", True)
    with patch("app.main.start_warm_up") as start, TestClient(app) as client:
        assert client.get("/health").status_code == 200
    start.assert_called_once()


def test_warm_up_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "warmup_on_startup", False)
    with patch("app.main.start_warm_up") as start, TestClient(app):
        pass
    start.assert_not_called()


def test_the_background_thread_runs_warm_up():
    with patch("app.api.query.warm_up") as warm:
        query.start_warm_up().join(timeout=5)
    warm.assert_called_once()
