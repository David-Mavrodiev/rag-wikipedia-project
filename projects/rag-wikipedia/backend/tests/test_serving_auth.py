"""The edge: what each auth mode does to a request, and what it never does.

`serving/app.py` is the composition root - the one place allowed to put a
provider (a Clerk verifier) around the API, because tests/test_layering.py
forbids `app/` from importing one. These cover the behaviour that decides
whether a public demo stays usable and a protected one stays protected.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from providers.clerk import AuthError, Principal
from serving.app import ClerkAuthMiddleware, auth_mode, build_app


class _Verifier:
    """Accepts exactly one token; everything else is refused."""

    def __init__(self, good="good-token", principal=None):
        self.good = good
        self.principal = principal or Principal(user_id="user_1", groups=("public", "legal"))
        self.seen: list[str] = []

    def verify(self, token: str) -> Principal:
        self.seen.append(token)
        if token != self.good:
            raise AuthError("nope")
        return self.principal


def _client(mode: str, verifier=None) -> TestClient:
    api = FastAPI()

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.post("/query")
    def query(request_groups: None = None):
        return {"answer": "ok"}

    @api.get("/whoami")
    def whoami(request: object = None):
        return {"ok": True}

    api.add_middleware(ClerkAuthMiddleware, verifier=verifier, mode=mode)
    return TestClient(api)


# --- off ----------------------------------------------------------------------

def test_off_lets_everything_through_and_identifies_nobody():
    response = _client("off").post("/query")
    assert response.status_code == 200
    assert response.headers["X-Auth"] == "anonymous"


# --- optional: what the public demo runs --------------------------------------

def test_optional_answers_without_a_token():
    response = _client("optional", _Verifier()).post("/query")
    assert response.status_code == 200
    assert response.headers["X-Auth"] == "anonymous"


def test_optional_identifies_a_valid_token():
    response = _client("optional", _Verifier()).post(
        "/query", headers={"Authorization": "Bearer good-token"})
    assert response.status_code == 200
    assert response.headers["X-Auth"] == "user"


def test_a_bad_token_is_refused_even_in_optional():
    # Presenting a broken token is not the same as presenting none: the first is
    # a problem the caller must see, the second is an anonymous visitor.
    response = _client("optional", _Verifier()).post(
        "/query", headers={"Authorization": "Bearer forged"})
    assert response.status_code == 401


# --- required -----------------------------------------------------------------

def test_required_refuses_an_anonymous_request():
    assert _client("required", _Verifier()).post("/query").status_code == 401


def test_required_allows_a_valid_token():
    response = _client("required", _Verifier()).post(
        "/query", headers={"Authorization": "Bearer good-token"})
    assert response.status_code == 200


def test_health_stays_open_in_every_mode():
    # A monitor that must authenticate reports an outage when auth breaks.
    for mode in ("off", "optional", "required"):
        assert _client(mode, _Verifier()).get("/health").status_code == 200


def test_a_refusal_does_not_say_which_check_failed():
    response = _client("required", _Verifier()).post(
        "/query", headers={"Authorization": "Bearer forged"})
    assert response.json() == {"detail": "Not authenticated"}


# --- the composition root ------------------------------------------------------

def test_auth_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    assert auth_mode() == "off"


def test_an_unknown_mode_falls_back_to_off_rather_than_guessing(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "sort-of")
    assert auth_mode() == "off"


def test_a_mode_without_an_issuer_does_not_lock_everyone_out(monkeypatch, caplog):
    # A missing key must disable the feature, not shut a running demo.
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    built = build_app()
    with TestClient(built) as client:
        assert client.get("/health").status_code == 200
    assert any("stays off" in record.message for record in caplog.records)


def test_the_api_is_unchanged_when_auth_is_off(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    with TestClient(build_app()) as client:
        assert client.get("/health").json()["status"] == "ok"
