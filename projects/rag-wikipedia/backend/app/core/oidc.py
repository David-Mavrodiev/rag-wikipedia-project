"""Generic OpenID Connect sign-in for enterprise tenants.

One code path serves every IdP: endpoints and signing keys come from the
provider's discovery document, so Entra ID, Okta, Auth0 and Keycloak differ only
by the issuer stored on the organization.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from app.core.oauth import code_challenge
from app.db.models import ROLE_ADMIN, ROLE_MEMBER

logger = logging.getLogger(__name__)

# Asymmetric algorithms only. Accepting HS* would let anyone who knows the client
# secret — which is shared configuration, not a signing key — mint id_tokens.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "ES256", "ES384"]

SCOPE = "openid email profile"

_TIMEOUT = httpx.Timeout(10.0)
_METADATA_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 900

_metadata_cache: dict[str, tuple[float, OIDCMetadata]] = {}
_jwks_cache: dict[str, tuple[float, PyJWKSet]] = {}


class OIDCError(Exception):
    """The handshake failed; the caller turns this into a redirect."""


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class OIDCIdentity:
    subject: str
    email: str
    name: str
    groups: list[str]


def clear_caches() -> None:
    _metadata_cache.clear()
    _jwks_cache.clear()


async def get_metadata(issuer: str) -> OIDCMetadata:
    key = issuer.rstrip("/")
    cached = _metadata_cache.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    document = await _get_json(f"{key}/.well-known/openid-configuration")
    metadata = _parse_metadata(document)
    # The document has to claim the issuer we asked for. Without this check a
    # mistyped or hijacked discovery URL could quietly point sign-in at another
    # provider while every later check still "passes".
    if metadata.issuer.rstrip("/") != key:
        raise OIDCError("discovery document issuer does not match the configured issuer")

    _metadata_cache[key] = (time.monotonic() + _METADATA_TTL_SECONDS, metadata)
    return metadata


def build_authorize_url(
    metadata: OIDCMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_verifier: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    metadata: OIDCMetadata,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> str:
    """Swap the authorization code for an id_token."""
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.post(
                metadata.token_endpoint, data=data, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise OIDCError("token endpoint unreachable") from exc

    if response.status_code != 200:
        raise OIDCError(f"token exchange returned {response.status_code}")
    id_token = _json(response).get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise OIDCError("token response carried no id_token")
    return id_token


async def verify_id_token(
    id_token: str, metadata: OIDCMetadata, *, client_id: str, nonce: str
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.InvalidTokenError as exc:
        raise OIDCError("malformed id_token") from exc

    key = await _signing_key(metadata.jwks_uri, header.get("kid"))
    try:
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=ALGORITHMS,
            audience=client_id,
            issuer=metadata.issuer,
            # Reject a token that simply omits a claim we rely on, instead of
            # treating "absent" as "nothing to check".
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise OIDCError(f"id_token rejected: {exc}") from exc

    # Binds the token to the sign-in this browser actually started, so a token
    # captured elsewhere cannot be replayed into someone else's session.
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("id_token nonce does not match")
    return claims


def identity_from_claims(claims: dict[str, Any], groups_claim: str) -> OIDCIdentity:
    subject = claims.get("sub")
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not subject or "@" not in email:
        raise OIDCError("id_token carries no usable subject or email")

    # Entra ID replaces the groups claim with a _claim_names pointer once a user
    # is in more than ~200 groups; such a user simply maps to no roles here.
    raw_groups = claims.get(groups_claim) or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups = [str(group) for group in raw_groups if group] if isinstance(raw_groups, list) else []

    return OIDCIdentity(
        subject=str(subject),
        email=email,
        name=str(claims.get("name") or ""),
        groups=groups,
    )


def role_for_groups(groups: list[str], role_mappings: dict[str, str]) -> str:
    """Map IdP groups to a local role; the strongest mapped role wins."""
    mapped = {role_mappings[group] for group in groups if group in role_mappings}
    return ROLE_ADMIN if ROLE_ADMIN in mapped else ROLE_MEMBER


async def _signing_key(jwks_uri: str, kid: str | None) -> PyJWK:
    key = _find_key(await _get_jwks(jwks_uri), kid)
    if key is None:
        # An unknown kid normally means the provider rotated its keys since we
        # cached them, so refetch once before giving up.
        key = _find_key(await _get_jwks(jwks_uri, force=True), kid)
    if key is None:
        raise OIDCError("no signing key matches the id_token")
    return key


async def _get_jwks(jwks_uri: str, *, force: bool = False) -> PyJWKSet:
    cached = _jwks_cache.get(jwks_uri)
    if not force and cached is not None and cached[0] > time.monotonic():
        return cached[1]
    try:
        jwks = PyJWKSet.from_dict(await _get_json(jwks_uri))
    except (jwt.PyJWKSetError, KeyError, TypeError, AttributeError) as exc:
        raise OIDCError("signing key set could not be parsed") from exc
    _jwks_cache[jwks_uri] = (time.monotonic() + _JWKS_TTL_SECONDS, jwks)
    return jwks


def _find_key(jwks: PyJWKSet, kid: str | None) -> PyJWK | None:
    for key in jwks.keys:
        if kid is None or key.key_id == kid:
            return key
    return None


def _parse_metadata(document: Any) -> OIDCMetadata:
    if not isinstance(document, dict):
        raise OIDCError("discovery document is not an object")
    required = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
    values = {name: document.get(name) for name in required}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise OIDCError("discovery document is missing required endpoints")
    return OIDCMetadata(**values)  # type: ignore[arg-type]


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OIDCError(f"{url} unreachable") from exc
    if response.status_code != 200:
        raise OIDCError(f"{url} returned {response.status_code}")
    return _json(response)


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise OIDCError("provider returned a non-JSON response") from exc
