"""The composition root: the API, plus whatever the edge decides to put around it.

`app/` declares interfaces and serves requests; `providers/` implements adapters
for the outside world; and `tests/test_layering.py` forbids the first from
importing the second. Authentication needs both - a Clerk verifier is an adapter,
and the thing it protects is the API - so it is wired HERE, in a package that may
import both, and that nothing in `app/` imports back.

That is also the answer to a note this repository has carried for a while: the
registries could select a provider, but `/query` could not dispatch through them
without `app/` reaching for `providers/`. This module is the missing edge.

    uvicorn serving.app:app       # the API with auth composed in
    uvicorn app.main:app          # the API alone, exactly as before

THREE MODES, and the default keeps the public demo public:

    off       no authentication at all. The default, and what runs when no
              Clerk issuer is configured - a missing key disables the feature
              rather than locking everyone out of a running demo.
    optional  a token is verified when present and the request is identified;
              without one it proceeds as anonymous. This is what the demo wants:
              a recruiter must be able to ask a question without signing up,
              and a signed-in user can be given more.
    required  no valid token, no answer: 401.
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

MODES = ("off", "optional", "required")
# Health and metrics stay reachable without a token in every mode: a monitor
# that has to authenticate is a monitor that reports an outage when auth breaks.
OPEN_PATHS = ("/health", "/healthz", "/metrics", "/docs", "/openapi.json")


def auth_mode() -> str:
    mode = os.getenv("AUTH_MODE", "off").strip().lower()
    return mode if mode in MODES else "off"


class ClerkAuthMiddleware(BaseHTTPMiddleware):
    """Attaches a verified Principal to every request, or refuses it.

    The principal lands on `request.state.principal`, so handlers read an
    ordinary attribute and never import a provider. `request.state.groups` is
    the same thing in the shape the Azure store wants, which is how "who is
    asking" reaches "what the index will return".
    """

    def __init__(self, app, verifier=None, mode: str | None = None) -> None:
        super().__init__(app)
        self._verifier = verifier
        self._mode = mode or auth_mode()

    async def dispatch(self, request, call_next):
        from providers.clerk import AuthError, Principal, bearer_token  # noqa: PLC0415

        principal = Principal.anonymous()
        if self._mode != "off" and request.url.path not in OPEN_PATHS:
            token = bearer_token(request.headers.get("authorization"))
            if token and self._verifier is not None:
                try:
                    principal = self._verifier.verify(token)
                except AuthError as exc:
                    # Never say WHICH check failed: that tells a prober whether
                    # a token was ours, expired, or simply wrong.
                    logger.info("rejected a token: %s", exc)
                    return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            elif self._mode == "required":
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        request.state.principal = principal
        request.state.groups = list(principal.groups)
        response = await call_next(request)
        # Useful in a browser's network tab while setting Clerk up, and harmless:
        # it says whether the request was identified, never who it was.
        response.headers["X-Auth"] = "user" if principal.signed_in else "anonymous"
        return response


def build_verifier():
    """A verifier when Clerk is configured, else None (the feature stays off)."""
    issuer = os.getenv("CLERK_ISSUER", "").strip()
    if not issuer:
        return None
    from providers.clerk import ClerkVerifier  # noqa: PLC0415

    return ClerkVerifier(issuer, audience=os.getenv("CLERK_AUDIENCE") or None)


def build_app(verifier=None, mode: str | None = None):
    """The served application. Importing `app.main` here, never the reverse."""
    from app.main import app as api  # noqa: PLC0415

    resolved = mode or auth_mode()
    verifier = verifier if verifier is not None else build_verifier()
    if resolved != "off" and verifier is None:
        logger.warning("AUTH_MODE=%s but no CLERK_ISSUER: authentication stays off", resolved)
        resolved = "off"

    if resolved != "off":
        api.add_middleware(ClerkAuthMiddleware, verifier=verifier, mode=resolved)
        logger.info("authentication: %s (issuer %s)", resolved, os.getenv("CLERK_ISSUER"))
    else:
        logger.info("authentication: off")
    return api


app = build_app()
