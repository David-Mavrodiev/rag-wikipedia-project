"""Password hashing, server-side sessions, and the authentication dependencies.

The cookie carries a short-lived HS256 JWT, but the JWT is only a pointer: the
session itself is a database row. That is what makes revocation, idle timeout
and deactivation take effect immediately instead of whenever a token happens to
expire.
"""

from __future__ import annotations

import logging
import secrets

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import audit
from app.db import sessions as sessions_repo
from app.db import users as users_repo
from app.db.models import ROLE_ADMIN, User, ensure_utc, utcnow
from app.db.models import Session as SessionRecord
from app.db.session import get_session

logger = logging.getLogger(__name__)

# Pinned, deliberately not configurable. Accepting whatever `alg` a token asks
# for is the classic JWT forgery path ("none", or an RS256 public key replayed
# as an HMAC secret), so verification only ever allows this one algorithm.
ALGORITHM = "HS256"

# bcrypt ignores everything past 72 bytes. Reject instead of truncating so a
# long passphrase is never silently reduced to its prefix.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8

# Hashed once at import: verifying against it lets a missing account cost the
# same as an existing one, so response time cannot be used to enumerate users.
_DUMMY_HASH = bcrypt.hashpw(b"no-such-user", bcrypt.gensalt())

# Fallback signing key when JWT_SECRET is unset. Regenerated per process, which
# means every restart invalidates previously issued tokens.
_EPHEMERAL_SECRET = secrets.token_urlsafe(64)
_warned_ephemeral = False

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password with a fresh random salt (bcrypt embeds it in the digest)."""
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))
    except ValueError:
        # A corrupt stored hash must fail closed, not 500.
        logger.warning("Ignoring malformed password hash")
        return False


async def authenticate_user(
    session: AsyncSession, identifier: str, password: str
) -> User | None:
    """Return the user when email/username + password match, else None."""
    user = await users_repo.get_by_identifier(session, identifier)
    if user is None or user.password_hash is None or not user.is_active:
        # Spend the same bcrypt work whether the account is missing, disabled or
        # social-only, so timing does not reveal which (see _DUMMY_HASH).
        bcrypt.checkpw(password.encode("utf-8")[:MAX_PASSWORD_BYTES], _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session_token(record: SessionRecord) -> tuple[str, int]:
    """Sign a cookie value for `record`; returns (token, cookie max-age)."""
    expires_at = ensure_utc(record.expires_at)
    payload = {
        # The session id is the part that matters: it is what lets the server
        # end this login early.
        "sid": record.id,
        "sub": record.user_id,
        "iat": utcnow(),
        "exp": expires_at,
    }
    token = jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)
    return token, max(int((expires_at - utcnow()).total_seconds()), 0)


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        # httponly: script on the page cannot read the token, so an XSS bug
        #   cannot exfiltrate the session.
        # samesite=lax: the browser withholds the cookie on cross-site POSTs
        #   (the CSRF baseline for /query) but still sends it on the top-level
        #   redirect back from an identity provider, which callbacks depend on.
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.cookie_domain or None,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        domain=settings.cookie_domain or None,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: resolve the caller's live session or raise 401."""
    token = request.cookies.get(settings.session_cookie_name)
    if token is None and credentials is not None:
        # Bearer fallback for scripts and curl, which have no cookie jar.
        token = credentials.credentials
    if token is None:
        raise _unauthorized("Not authenticated")

    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Session expired") from exc
    except jwt.InvalidTokenError as exc:
        # Bad signature, wrong algorithm, malformed token — all indistinguishable
        # to the caller on purpose.
        raise _unauthorized("Invalid session") from exc

    session_id = payload.get("sid")
    if not isinstance(session_id, str):
        raise _unauthorized("Invalid session")

    record = await sessions_repo.get(db, session_id)
    if record is None:
        raise _unauthorized("Invalid session")

    reason = sessions_repo.expiry_reason(record)
    if reason is not None:
        if reason != sessions_repo.REASON_REVOKED:
            # Close it out so the same cookie cannot come back to life if the
            # clock or the configuration moves.
            await sessions_repo.revoke(db, record)
            await audit.record(
                db, audit.EVENT_SESSION_ENDED, user=record.user, ip=_client_ip(request),
                detail=reason,
            )
        raise _unauthorized("Session expired")

    user = record.user
    if not user.is_active:
        # Deactivation is immediate: drop every session the account still holds.
        await sessions_repo.revoke_all_for_user(db, user.id)
        raise _unauthorized("Account is disabled")

    await sessions_repo.touch(db, record)
    # Lets /auth/sessions tell "this browser" from the others.
    request.state.session_id = record.id
    return user


async def require_admin(request: Request, user: User = Depends(require_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required"
        )
    return user


async def session_from_request(request: Request, db: AsyncSession) -> SessionRecord | None:
    """Best-effort session lookup that never raises.

    Logout has to work from a cookie that is already expired or forged, so it
    cannot go through the strict dependency.
    """
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        return None
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    session_id = payload.get("sid")
    if not isinstance(session_id, str):
        return None
    return await sessions_repo.get(db, session_id)


def client_ip(request: Request) -> str | None:
    return _client_ip(request)


def _client_ip(request: Request) -> str | None:
    # X-Forwarded-For is caller-controlled unless a trusted proxy rewrites it;
    # it is recorded for the audit trail only, never used for authorization.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def _signing_key() -> str:
    global _warned_ephemeral
    if settings.jwt_secret:
        return settings.jwt_secret
    if not _warned_ephemeral:
        logger.warning("JWT_SECRET is not set — using an ephemeral per-process signing key")
        _warned_ephemeral = True
    return _EPHEMERAL_SECRET


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
