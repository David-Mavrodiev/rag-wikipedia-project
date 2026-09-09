import pytest
from app.core import quality as quality_state
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_quality_state():
    """`update_quality_state` mutates a module-level singleton.

    Without this, a test that runs an audit leaves status == "healthy" behind
    and any later test reading GET /quality sees it, so the suite passes or
    fails depending on collection order.
    """
    quality_state.reset_quality_state()
    yield
    quality_state.reset_quality_state()


@pytest.fixture
def client(monkeypatch):
    # monkeypatch, not direct assignment: settings is a process-wide singleton,
    # so a plain write leaked the disabled limiter into every later test.
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    return TestClient(app)
