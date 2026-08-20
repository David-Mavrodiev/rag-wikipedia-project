"""Server-side session behaviour: idle expiry, absolute lifetime, revocation."""

from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient

COOKIE = "rag_session"


def second_browser() -> TestClient:
    # A separate cookie jar against the same app and database. The lifespan has
    # already run for this test, so no context manager is needed.
    return TestClient(app)


def sign_in(browser: TestClient, credentials) -> TestClient:
    assert browser.post("/auth/login", json={
        "identifier": credentials["email"],
        "password": credentials["password"],
    }).status_code == 200
    return browser


# --- listing ----------------------------------------------------------------


def test_current_session_is_listed_and_marked(client):
    sessions = client.get("/auth/sessions").json()

    assert len(sessions) == 1
    assert sessions[0]["current"] is True
    assert sessions[0]["expires_at"] > sessions[0]["created_at"]


def test_each_sign_in_creates_its_own_session(client, credentials):
    sign_in(second_browser(), credentials)

    sessions = client.get("/auth/sessions").json()
    assert len(sessions) == 2
    assert [entry["current"] for entry in sessions].count(True) == 1


def test_session_records_the_user_agent(client):
    sessions = client.get("/auth/sessions").json()

    # Captured when the session was created, so a user can recognise a browser
    # they do not remember signing in from.
    assert sessions[0]["user_agent"] is not None


# --- expiry -----------------------------------------------------------------


def test_idle_timeout_ends_the_session(client, monkeypatch):
    assert client.get("/auth/me").status_code == 200
    # An abandoned browser: nothing has touched the session inside the window.
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 0)

    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Session expired"


def test_idle_expiry_is_permanent(client, monkeypatch):
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 0)
    client.get("/auth/me")
    # Restoring the window must not resurrect a session that already lapsed.
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 30)

    assert client.get("/auth/me").status_code == 401


def test_absolute_lifetime_caps_an_active_session(anon_client, credentials, monkeypatch):
    anon_client.post("/auth/register", json=credentials)
    monkeypatch.setattr(settings, "session_absolute_lifetime_hours", 0)
    anon_client.cookies.clear()
    sign_in(anon_client, credentials)

    # Constant use cannot push the session past its ceiling.
    assert anon_client.get("/auth/me").status_code == 401


# --- revocation -------------------------------------------------------------


def test_logout_revokes_the_session_on_the_server(client):
    stolen = client.cookies.get(COOKIE)
    assert client.post("/auth/logout").status_code == 204

    # Replay the cookie the browser was told to drop: the session row is what
    # decides, so it must be refused.
    client.cookies.set(COOKIE, stolen)
    assert client.get("/auth/me").status_code == 401


def test_sign_out_everywhere_keeps_the_current_browser(client, credentials):
    other = sign_in(second_browser(), credentials)

    assert client.delete("/auth/sessions").status_code == 204
    assert other.get("/auth/me").status_code == 401
    assert client.get("/auth/me").status_code == 200
    assert len(client.get("/auth/sessions").json()) == 1


def test_deactivating_an_account_cuts_live_sessions_immediately(
    admin_client, anon_client, credentials
):
    victim = {
        "email": "victim@example.com",
        "username": "victim",
        "password": credentials["password"],
    }
    anon_client.post("/auth/register", json=victim)
    victim_id = anon_client.get("/auth/me").json()["id"]
    assert anon_client.get("/auth/me").status_code == 200

    assert admin_client.post(f"/admin/users/{victim_id}/deactivate").status_code == 200

    # No waiting for a token to expire.
    assert anon_client.get("/auth/me").status_code == 401


def test_a_deactivated_account_cannot_sign_in_again(admin_client, anon_client, credentials):
    victim = {
        "email": "victim@example.com",
        "username": "victim",
        "password": credentials["password"],
    }
    anon_client.post("/auth/register", json=victim)
    victim_id = anon_client.get("/auth/me").json()["id"]
    admin_client.post(f"/admin/users/{victim_id}/deactivate")
    anon_client.cookies.clear()

    response = anon_client.post(
        "/auth/login", json={"identifier": victim["email"], "password": victim["password"]}
    )
    # Same generic answer as any other failure: no "your account is disabled"
    # oracle for someone guessing addresses.
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email/username or password"
