"""Clerk session tokens, verified here rather than trusted.

WHY THIS EXISTS AT ALL, GIVEN JOBHACK DOES IT DIFFERENTLY. JobHack puts Clerk in
a Next.js app and never lets a browser reach the model service: server routes
sit in between, holding a secret the browser never sees. This project has no
server in between - a static page on Vercel calls FastAPI on Modal directly - so
a sign-in button alone would decorate an open API. The token therefore has to be
verified where the work happens.

HOW IT IS VERIFIED. Clerk signs session tokens with RS256 and publishes the
public keys at `<issuer>/.well-known/jwks.json`. The signature, the issuer, the
expiry and the not-before are all checked; nothing is read out of an unverified
token. The key set is cached, and refetched once when a token names a key id
that is not in the cache - which is what happens, legitimately, after Clerk
rotates a key.

WHAT COMES OUT. A `Principal`: the Clerk user id, the organisation when there is
one, and the groups it grants. Those groups are the same strings the Azure AI
Search store filters on (`providers/azure_search.py`), so "who you are" and
"what the index will return to you" are one chain rather than two systems that
agree by accident.

PyJWT is an optional extra, imported inside the methods that use it, like every
other provider here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

JWKS_PATH = "/.well-known/jwks.json"
LEEWAY_S = 10
# A key set is refetched at most this often, however many unknown kids arrive -
# an attacker sending random key ids must not become a request amplifier.
MIN_REFETCH_INTERVAL_S = 30


class AuthError(Exception):
    """The token is missing, malformed, expired or not ours."""


@dataclass(frozen=True)
class Principal:
    """Who is asking. `anonymous()` is a real answer, not an error."""

    user_id: str | None = None
    org_id: str | None = None
    groups: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def signed_in(self) -> bool:
        return self.user_id is not None

    @classmethod
    def anonymous(cls) -> Principal:
        return cls()


def groups_from_claims(claims: dict[str, Any]) -> tuple[str, ...]:
    """The groups a token grants, from the places Clerk can carry them.

    Three sources, in order of how deliberate they are: an explicit `groups`
    claim (a Clerk JWT template), the organisation role (`org_role`), and the
    organisation slug. Everyone signed in also gets `public`, which is what the
    demo corpus is tagged with - so signing in never takes access away.
    """
    groups: list[str] = ["public"]
    explicit = claims.get("groups") or claims.get("acl_groups") or []
    if isinstance(explicit, str):
        explicit = [part.strip() for part in explicit.split(",") if part.strip()]
    groups.extend(str(group) for group in explicit)
    for key in ("org_role", "org_slug"):
        value = claims.get(key)
        if value:
            groups.append(str(value).replace(":", "-"))
    # Stable order, no duplicates: this ends up in a filter string, and a filter
    # that changes shape per request is a cache that never hits.
    return tuple(sorted(set(groups)))


class ClerkVerifier:
    """Verifies Clerk session tokens against the issuer's published keys."""

    def __init__(self, issuer: str, *, audience: str | None = None,
                 http_get=None, now=time.time) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._http_get = http_get
        self._now = now
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    # --- the key set ----------------------------------------------------------

    def _fetch_jwks(self) -> dict[str, Any]:
        if self._http_get is not None:
            return self._http_get(f"{self._issuer}{JWKS_PATH}")
        import httpx  # noqa: PLC0415

        response = httpx.get(f"{self._issuer}{JWKS_PATH}", timeout=10)
        response.raise_for_status()
        return response.json()

    def _key_for(self, kid: str) -> Any:
        if kid in self._keys:
            return self._keys[kid]
        if self._now() - self._fetched_at < MIN_REFETCH_INTERVAL_S and self._keys:
            raise AuthError("unknown signing key")

        from jwt import PyJWK  # noqa: PLC0415

        jwks = self._fetch_jwks()
        self._keys = {key["kid"]: PyJWK(key).key for key in jwks.get("keys", []) if "kid" in key}
        self._fetched_at = self._now()
        if kid not in self._keys:
            raise AuthError("unknown signing key")
        return self._keys[kid]

    # --- verification ---------------------------------------------------------

    def verify(self, token: str) -> Principal:
        """A verified Principal, or AuthError. Never a half-trusted token."""
        import jwt  # noqa: PLC0415

        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except Exception as exc:  # noqa: BLE001 - any malformed token lands here
            raise AuthError("malformed token") from exc
        if not kid:
            raise AuthError("token has no key id")

        options = {"verify_aud": self._audience is not None}
        try:
            claims = jwt.decode(
                token,
                key=self._key_for(kid),
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                leeway=LEEWAY_S,
                options=options,
            )
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - PyJWT raises a family of these
            raise AuthError(f"rejected token: {exc}") from exc

        return Principal(
            user_id=claims.get("sub"),
            org_id=claims.get("org_id"),
            groups=groups_from_claims(claims),
            claims=claims,
        )


def bearer_token(header: str | None) -> str | None:
    """The token out of an Authorization header, or None. Case-insensitive."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
