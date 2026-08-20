import pytest
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    settings.rate_limit_enabled = False
    return TestClient(app)
