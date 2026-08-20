from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from app.core import oauth
from app.core.config import settings


@pytest.fixture(autouse=True)
def oauth_apps(monkeypatch):
    """Both providers configured; individual tests unset one to test the gap."""
    monkeypatch.setattr(settings, "google_client_id", "google-id")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "github_client_id", "github-id")
    monkeypatch.setattr(settings, "github_client_secret", "github-secret")


def _profile(**overrides) -> oauth.OAuthProfile:
    defaults = {
        "provider": "google",
        "account_id": "google-123",
        "email": "social@example.com",
        "email_verified": True,
        "suggested_username": "social",
    }
    return oauth.OAuthProfile(**{**defaults, **overrides})


@contextmanager
def provider_returns(profile: oauth.OAuthProfile):
    """Stub the two network calls of the handshake."""
    with (
        patch("app.core.oauth.exchange_code", AsyncMock(return_value="provider-token")),
        patch("app.core.oauth.fetch_profile", AsyncMock(return_value=profile)),
    ):
        yield


def _authorize(client, provider: str = "google"):
    response = client.get(f"/auth/oauth/{provider}/authorize", follow_redirects=False)
    assert response.status_code == 302
    return response


def _callback(client, provider: str = "google", **overrides):
    params = {"code": "auth-code", "state": client.cookies.get("oauth_state"), **overrides}
    return client.get(
        f"/auth/oauth/{provider}/callback", params=params, follow_redirects=False
    )


# --- authorize --------------------------------------------------------------


def test_google_authorize_redirects_with_state_and_pkce(anon_client):
    response = _authorize(anon_client)

    location = urlparse(response.headers["location"])
    params = parse_qs(location.query)
    assert location.netloc == "accounts.google.com"
    assert params["client_id"] == ["google-id"]
    assert params["redirect_uri"] == ["http://api.test/auth/oauth/google/callback"]
    assert params["code_challenge_method"] == ["S256"]
    # The state we send must be the one we remembered, or the callback check is
    # meaningless.
    assert params["state"] == [anon_client.cookies.get("oauth_state")]


def test_github_authorize_omits_pkce(anon_client):
    # GitHub OAuth Apps ignore PKCE, so sending a challenge would only pretend.
    response = _authorize(anon_client, "github")

    params = parse_qs(urlparse(response.headers["location"]).query)
    assert "code_challenge" not in params
    assert params["state"] == [anon_client.cookies.get("oauth_state")]


def test_handshake_cookies_are_httponly(anon_client):
    response = _authorize(anon_client)

    cookies = " ".join(response.headers.get_list("set-cookie")).lower()
    assert "oauth_state=" in cookies
    assert "httponly" in cookies
    assert "path=/auth" in cookies


def test_unknown_provider_is_404(anon_client):
    assert anon_client.get("/auth/oauth/myspace/authorize").status_code == 404


def test_unconfigured_provider_is_503(anon_client, monkeypatch):
    monkeypatch.setattr(settings, "github_client_id", "")
    assert anon_client.get("/auth/oauth/github/authorize").status_code == 503


# --- callback ---------------------------------------------------------------


def test_callback_creates_a_social_account_and_signs_in(anon_client):
    _authorize(anon_client)
    with provider_returns(_profile()):
        response = _callback(anon_client)

    assert response.status_code == 302
    assert response.headers["location"] == settings.frontend_url
    me = anon_client.get("/auth/me").json()
    assert me["email"] == "social@example.com"
    assert me["username"] == "social"
    assert me["providers"] == ["google"]
    assert me["has_password"] is False


def test_social_account_cannot_sign_in_with_a_password(anon_client):
    _authorize(anon_client)
    with provider_returns(_profile()):
        _callback(anon_client)
    anon_client.cookies.clear()

    response = anon_client.post(
        "/auth/login", json={"identifier": "social@example.com", "password": "anything"}
    )
    assert response.status_code == 401


def test_second_sign_in_reuses_the_same_account(anon_client):
    _authorize(anon_client)
    with provider_returns(_profile()):
        _callback(anon_client)
    first_id = anon_client.get("/auth/me").json()["id"]

    anon_client.cookies.clear()
    _authorize(anon_client)
    # Same provider account, but the user renamed their mailbox at the provider.
    with provider_returns(_profile(email="moved@example.com")):
        _callback(anon_client)

    assert anon_client.get("/auth/me").json()["id"] == first_id


def test_callback_links_to_an_existing_account_with_the_same_email(anon_client, credentials):
    registered = anon_client.post(
        "/auth/register", json={**credentials, "email": "social@example.com"}
    ).json()
    anon_client.cookies.clear()

    _authorize(anon_client)
    with provider_returns(_profile()):
        _callback(anon_client)

    me = anon_client.get("/auth/me").json()
    assert me["id"] == registered["id"]
    assert me["providers"] == ["google"]
    assert me["has_password"] is True  # the password login still works


def test_callback_refuses_an_unverified_email(anon_client):
    # Otherwise anyone able to claim an unverified address at the provider could
    # take over the local account that owns it.
    _authorize(anon_client)
    with provider_returns(_profile(email_verified=False)):
        response = _callback(anon_client)

    assert "auth_error=email_unverified" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_rejects_a_state_we_did_not_issue(anon_client):
    _authorize(anon_client)
    with provider_returns(_profile()):
        response = _callback(anon_client, state="attacker-supplied-state")

    assert "auth_error=oauth_failed" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_without_a_handshake_cookie_is_rejected(anon_client):
    # No /authorize first: nothing proves this browser started the flow.
    with provider_returns(_profile()):
        response = _callback(anon_client, state="some-state")

    assert "auth_error=oauth_failed" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_passes_provider_errors_through_as_a_failure(anon_client):
    _authorize(anon_client)
    response = _callback(anon_client, error="access_denied", code="")

    assert "auth_error=oauth_failed" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_handles_a_failed_token_exchange(anon_client):
    _authorize(anon_client)
    with patch(
        "app.core.oauth.exchange_code", AsyncMock(side_effect=oauth.OAuthError("bad code"))
    ):
        response = _callback(anon_client)

    assert "auth_error=oauth_failed" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_usernames_are_deduplicated(anon_client, credentials):
    anon_client.post("/auth/register", json={**credentials, "username": "social"})
    anon_client.cookies.clear()

    _authorize(anon_client)
    with provider_returns(_profile()):
        _callback(anon_client)

    assert anon_client.get("/auth/me").json()["username"] == "social2"


# --- provider payload parsing -----------------------------------------------


def test_github_email_prefers_the_primary_verified_address():
    email, verified = oauth._primary_github_email(
        [
            {"email": "old@example.com", "primary": False, "verified": True},
            {"email": "main@example.com", "primary": True, "verified": True},
        ]
    )
    assert (email, verified) == ("main@example.com", True)


def test_github_email_never_reports_an_unverified_address_as_verified():
    email, verified = oauth._primary_github_email(
        [{"email": "unverified@example.com", "primary": True, "verified": False}]
    )
    assert (email, verified) == ("unverified@example.com", False)


def test_google_profile_carries_the_verification_flag():
    profile = oauth._google_profile(
        {"sub": "42", "email": "Person@Example.com", "email_verified": False}
    )
    assert profile.account_id == "42"
    assert profile.email == "person@example.com"
    assert profile.email_verified is False


def test_code_challenge_matches_the_rfc_7636_example():
    # Appendix B of RFC 7636 — proves the S256 challenge is base64url(sha256())
    # without padding, which is what Google verifies against.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert oauth.code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
