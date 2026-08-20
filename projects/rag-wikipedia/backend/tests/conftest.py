import pytest
from app.core.config import settings

# Tracing has to be on BEFORE app.main is imported: configure_tracing() runs at
# import time and adds middleware, and Starlette freezes the middleware stack on
# the first request - so a fixture would be too late. Enabling it here also
# means the whole suite runs against the instrumented path instead of leaving it
# to production to find out.
settings.tracing_enabled = True

from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    settings.rate_limit_enabled = False
    return TestClient(app)
