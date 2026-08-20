import bcrypt
import pytest
from app.core import oidc
from app.core.config import settings
from app.db import session as db_session
from app.main import app
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

TEST_EMAIL = "tester@example.com"
TEST_USERNAME = "tester"
TEST_PASSWORD = "correct-horse-battery-staple"
ADMIN_EMAIL = "admin@example.com"
# >= 32 bytes: PyJWT warns below that for HS256.
TEST_JWT_SECRET = "test-signing-key-not-for-production-use"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch):
    """Point every test at its own empty database with fixed keys."""
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite://")  # in-memory
    monkeypatch.setattr(settings, "jwt_secret", TEST_JWT_SECRET)
    monkeypatch.setattr(settings, "secret_encryption_key", TEST_ENCRYPTION_KEY)
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 30)
    monkeypatch.setattr(settings, "session_absolute_lifetime_hours", 8)
    monkeypatch.setattr(settings, "bootstrap_admin_email", "")
    # TestClient speaks plain http, and a Secure cookie would never be stored.
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "frontend_url", "http://frontend.test")
    monkeypatch.setattr(settings, "oauth_redirect_base_url", "http://api.test")

    # The engine and the OIDC discovery cache both outlive a single test, so
    # drop them or one test's fixtures would leak into the next.
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    oidc.clear_caches()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    oidc.clear_caches()


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    """Hash at cost 4 instead of the default 12.

    Almost every test registers or logs in, and the production cost would add
    ~250 ms to each of those calls. The cost factor is configuration, not
    behaviour: the salting and verification paths are unchanged.
    """
    real_gensalt = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=4: real_gensalt(rounds))


@pytest.fixture
def credentials():
    return {"email": TEST_EMAIL, "username": TEST_USERNAME, "password": TEST_PASSWORD}


@pytest.fixture
def jwt_secret():
    return TEST_JWT_SECRET


@pytest.fixture
def anon_client():
    """Signed-out client. The context manager runs startup, which creates tables."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(anon_client, credentials):
    """Default client: registered and signed in, since registration sets the cookie."""
    response = anon_client.post("/auth/register", json=credentials)
    assert response.status_code == 201
    return anon_client


@pytest.fixture
def registered_client(client):
    """Registered account, then signed out again — for exercising /auth/login."""
    client.cookies.clear()
    return client


@pytest.fixture
def admin_client(anon_client, monkeypatch):
    """Signed-in administrator, in a cookie jar of its own.

    Depends on `anon_client` only so the app lifespan has already created the
    tables; the two clients are deliberately separate browsers.
    """
    monkeypatch.setattr(settings, "bootstrap_admin_email", ADMIN_EMAIL)
    browser = TestClient(app)
    response = browser.post(
        "/auth/register",
        json={"email": ADMIN_EMAIL, "username": "admin", "password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    return browser
