"""Google and GitHub OAuth 2.0 (authorization code) support.

Written against httpx directly rather than a framework: the flow is two POSTs
and a GET per provider, and keeping it explicit makes the security-relevant
parts — `state`, PKCE, and email verification — visible and testable.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

_TIMEOUT = httpx.Timeout(10.0)


class OAuthError(Exception):
    """The provider handshake failed; the caller turns this into a redirect."""


@dataclass(frozen=True)
class Provider:
    name: str
    authorize_url: str
    token_url: str
    scope: str
    uses_pkce: bool


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    account_id: str
    email: str
    email_verified: bool
    suggested_username: str


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
        uses_pkce=True,
    ),
    "github": Provider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        # user:email is needed because /user hides the address unless it is public.
        scope="read:user user:email",
        # GitHub OAuth Apps ignore PKCE parameters, so sending them would only
        # give a false sense of protection — `state` is what guards this flow.
        uses_pkce=False,
    ),
}


def get_provider(name: str) -> Provider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")
    return provider


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def redirect_uri(provider: Provider) -> str:
    return (
        f"{settings.oauth_redirect_base_url.rstrip('/')}/auth/oauth/{provider.name}/callback"
    )


def build_authorize_url(provider: Provider, state: str, code_verifier: str) -> str:
    client_id, _ = _credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
    }
    if provider.uses_pkce:
        params["code_challenge"] = code_challenge(code_verifier)
        params["code_challenge_method"] = "S256"
    return f"{provider.authorize_url}?{urlencode(params)}"


async def exchange_code(provider: Provider, code: str, code_verifier: str) -> str:
    """Swap the one-time authorization code for an access token."""
    client_id, client_secret = _credentials(provider)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "grant_type": "authorization_code",
    }
    if provider.uses_pkce:
        data["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.post(
                provider.token_url, data=data, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"{provider.name} token endpoint unreachable") from exc

    if response.status_code != 200:
        raise OAuthError(f"{provider.name} token exchange returned {response.status_code}")
    # GitHub answers 200 with {"error": ...} for a replayed or expired code.
    payload = _json(response)
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthError(f"{provider.name} returned no access token")
    return access_token


async def fetch_profile(provider: Provider, access_token: str) -> OAuthProfile:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            if provider.name == "google":
                userinfo = await client.get(GOOGLE_USERINFO_URL, headers=headers)
                return _google_profile(_json(_ok(userinfo)))
            user = _json(_ok(await client.get(GITHUB_USER_URL, headers=headers)))
            emails = _json(_ok(await client.get(GITHUB_EMAILS_URL, headers=headers)))
            return _github_profile(user, emails)
        except httpx.HTTPError as exc:
            raise OAuthError(f"{provider.name} profile request failed") from exc


def _google_profile(data: dict[str, Any]) -> OAuthProfile:
    account_id = data.get("sub")
    email = (data.get("email") or "").strip().lower()
    if not isinstance(account_id, str) or not account_id or not email:
        raise OAuthError("google returned an incomplete profile")
    return OAuthProfile(
        provider="google",
        account_id=account_id,
        email=email,
        # Google reports this per address; anything else must not be trusted to
        # prove ownership of the mailbox.
        email_verified=bool(data.get("email_verified")),
        suggested_username=email.split("@")[0],
    )


def _github_profile(user: Any, emails: Any) -> OAuthProfile:
    account_id = user.get("id") if isinstance(user, dict) else None
    if account_id is None:
        raise OAuthError("github returned an incomplete profile")
    email, verified = _primary_github_email(emails)
    if not email:
        raise OAuthError("github returned no usable email address")
    return OAuthProfile(
        provider="github",
        account_id=str(account_id),
        email=email,
        email_verified=verified,
        suggested_username=(user.get("login") or email.split("@")[0]),
    )


def _primary_github_email(emails: Any) -> tuple[str, bool]:
    """Pick the primary verified address, falling back to any verified one."""
    if not isinstance(emails, list):
        return "", False
    entries = [entry for entry in emails if isinstance(entry, dict) and entry.get("email")]
    for entry in entries:
        if entry.get("primary") and entry.get("verified"):
            return str(entry["email"]).strip().lower(), True
    for entry in entries:
        if entry.get("verified"):
            return str(entry["email"]).strip().lower(), True
    return (str(entries[0]["email"]).strip().lower(), False) if entries else ("", False)


def _credentials(provider: Provider) -> tuple[str, str]:
    if provider.name == "google":
        client_id, client_secret = settings.google_client_id, settings.google_client_secret
    else:
        client_id, client_secret = settings.github_client_id, settings.github_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider.name} sign-in is not configured",
        )
    return client_id, client_secret


def code_challenge(verifier: str) -> str:
    """The S256 PKCE challenge: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _ok(response: httpx.Response) -> httpx.Response:
    if response.status_code != 200:
        raise OAuthError(f"provider API returned {response.status_code}")
    return response


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise OAuthError("provider returned a non-JSON response") from exc
