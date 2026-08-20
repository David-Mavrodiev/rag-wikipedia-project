from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import oauth, oidc
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import (
    authenticate_user,
    clear_session_cookie,
    client_ip,
    create_session_token,
    hash_password,
    require_user,
    session_from_request,
    set_session_cookie,
)
from app.db import audit
from app.db import organizations as orgs_repo
from app.db import sessions as sessions_repo
from app.db import users as users_repo
from app.db.models import ROLE_ADMIN, Organization, User
from app.db.models import Session as SessionRecord
from app.db.session import get_session
from app.models.auth import (
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    SSOStartRequest,
    SSOStartResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(require_user)]

# Short-lived cookies that carry a handshake across the round trip to the
# provider. Scoped to /auth so they are never attached to anything else.
_STATE_COOKIE = "oauth_state"
_VERIFIER_COOKIE = "oauth_verifier"
_NONCE_COOKIE = "oidc_nonce"
_ORG_COOKIE = "oidc_org"
_HANDSHAKE_COOKIES = (_STATE_COOKIE, _VERIFIER_COOKIE, _NONCE_COOKIE, _ORG_COOKIE)
_HANDSHAKE_PATH = "/auth"
_HANDSHAKE_TTL_SECONDS = 600


class _OAuthSignupRefused(Exception):
    """Provider handshake succeeded but the account may not be created/linked."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest, http_request: Request, response: Response, session: SessionDep
) -> UserResponse:
    # A managed domain must not be able to grow password accounts behind its
    # own SSO policy, so this is checked before anything is created.
    await _reject_if_sso_enforced(session, http_request, request.email)

    if (
        await users_repo.get_by_email(session, request.email) is not None
        or await users_repo.get_by_username(session, request.username) is not None
    ):
        raise _taken()

    try:
        user = await users_repo.create_user(
            session,
            email=request.email,
            username=request.username,
            password_hash=hash_password(request.password),
        )
    except IntegrityError as exc:
        # Lost a race with a concurrent signup; the unique indexes are the real
        # guarantee, the check above is only for a friendlier error.
        await session.rollback()
        raise _taken() from exc

    await _ensure_bootstrap_admin(session, user)
    await _start_session(session, http_request, response, user)
    await audit.record(
        session, audit.EVENT_REGISTERED, user=user, ip=client_ip(http_request)
    )
    return UserResponse.from_user(user)


@router.post("/login", response_model=UserResponse)
async def login(
    request: LoginRequest, http_request: Request, response: Response, session: SessionDep
) -> UserResponse:
    if "@" in request.identifier:
        await _reject_if_sso_enforced(session, http_request, request.identifier)

    user = await authenticate_user(session, request.identifier, request.password)
    if user is None:
        logger.warning("Failed login attempt for %r", request.identifier)
        await audit.record(
            session,
            audit.EVENT_LOGIN_FAILED,
            email=request.identifier[:320],
            ip=client_ip(http_request),
        )
        # One generic message for unknown account, wrong password, disabled
        # account and social-only account: anything more is an account oracle.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await _ensure_bootstrap_admin(session, user)
    await _start_session(session, http_request, response, user)
    await audit.record(
        session, audit.EVENT_LOGIN_SUCCEEDED, user=user, ip=client_ip(http_request)
    )
    return UserResponse.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(http_request: Request, session: SessionDep) -> Response:
    # The cookie is set on the returned response directly: FastAPI does not
    # merge the injected `Response` headers when a handler returns a Response.
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)

    # Deliberately not behind require_user: signing out has to work even from a
    # session that has already expired.
    record = await session_from_request(http_request, session)
    if record is not None:
        await sessions_repo.revoke(session, record)
        await audit.record(
            session, audit.EVENT_LOGOUT, user=record.user, ip=client_ip(http_request)
        )
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.from_user(user)


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    user: CurrentUser, http_request: Request, session: SessionDep
) -> list[SessionResponse]:
    records = await sessions_repo.list_active_for_user(session, user.id)
    current_id = getattr(http_request.state, "session_id", None)
    return [SessionResponse.from_session(record, current_id) for record in records]


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(
    user: CurrentUser, http_request: Request, session: SessionDep
) -> Response:
    """Sign out everywhere else — the usual response to a stolen laptop."""
    current_id = getattr(http_request.state, "session_id", None)
    revoked = await sessions_repo.revoke_all_for_user(session, user.id, except_id=current_id)
    await audit.record(
        session,
        audit.EVENT_SESSIONS_REVOKED,
        user=user,
        ip=client_ip(http_request),
        detail=f"{revoked} session(s) revoked by the account owner",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- enterprise single sign-on ----------------------------------------------


@router.post("/sso/start", response_model=SSOStartResponse)
async def sso_start(
    request: SSOStartRequest, response: Response, session: SessionDep
) -> SSOStartResponse:
    organization = await orgs_repo.get_for_email(session, request.email)
    if organization is None or not organization.is_active:
        # Domain-level, so this reveals nothing about any individual account.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No single sign-on is configured for that domain",
        )

    try:
        metadata = await oidc.get_metadata(organization.issuer)
    except oidc.OIDCError as exc:
        logger.warning("OIDC discovery failed for %s: %s", organization.name, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider is unavailable",
        ) from exc

    state = oauth.new_state()
    nonce = oauth.new_state()
    code_verifier = oauth.new_code_verifier()
    authorize_url = oidc.build_authorize_url(
        metadata,
        client_id=organization.client_id,
        redirect_uri=_sso_redirect_uri(),
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
    )

    _set_handshake_cookie(response, _STATE_COOKIE, state)
    _set_handshake_cookie(response, _NONCE_COOKIE, nonce)
    _set_handshake_cookie(response, _VERIFIER_COOKIE, code_verifier)
    _set_handshake_cookie(response, _ORG_COOKIE, organization.id)
    return SSOStartResponse(authorize_url=authorize_url)


@router.get("/sso/callback")
async def sso_callback(
    http_request: Request,
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    expected_state = http_request.cookies.get(_STATE_COOKIE)
    if error or not code or not state or not expected_state:
        return await _sso_failure(session, http_request, "sso_failed", "incomplete callback")
    if not secrets.compare_digest(state, expected_state):
        return await _sso_failure(session, http_request, "sso_failed", "state mismatch")

    organization = await orgs_repo.get_by_id(session, http_request.cookies.get(_ORG_COOKIE) or "")
    if organization is None or not organization.is_active:
        return await _sso_failure(session, http_request, "sso_failed", "unknown organization")

    # A configuration failure here (missing or wrong encryption key) is the
    # operator's problem, not a failed handshake, so it surfaces as a 503.
    client_secret = decrypt_secret(organization.client_secret_encrypted)

    try:
        metadata = await oidc.get_metadata(organization.issuer)
        id_token = await oidc.exchange_code(
            metadata,
            client_id=organization.client_id,
            client_secret=client_secret,
            code=code,
            code_verifier=http_request.cookies.get(_VERIFIER_COOKIE) or "",
            redirect_uri=_sso_redirect_uri(),
        )
        claims = await oidc.verify_id_token(
            id_token,
            metadata,
            client_id=organization.client_id,
            nonce=http_request.cookies.get(_NONCE_COOKIE) or "",
        )
        identity = oidc.identity_from_claims(claims, organization.groups_claim)
    except oidc.OIDCError as exc:
        logger.warning("SSO handshake failed for %s: %s", organization.name, exc)
        return await _sso_failure(session, http_request, "sso_failed", str(exc), organization)

    # The IdP is authoritative for the domains its organization has registered,
    # and for nothing else — otherwise a tenant could assert any address.
    if not orgs_repo.owns_domain(organization, identity.email):
        return await _sso_failure(
            session, http_request, "sso_domain_mismatch", "email outside tenant", organization
        )

    try:
        user = await _resolve_sso_user(session, organization, identity, http_request)
    except _OAuthSignupRefused as exc:
        return await _sso_failure(session, http_request, exc.code, exc.code, organization)

    response = RedirectResponse(settings.frontend_url, status_code=status.HTTP_302_FOUND)
    await _start_session(session, http_request, response, user)
    await audit.record(
        session,
        audit.EVENT_SSO_SUCCEEDED,
        user=user,
        organization_id=organization.id,
        ip=client_ip(http_request),
    )
    _clear_handshake_cookies(response)
    return response


async def _resolve_sso_user(
    session: AsyncSession,
    organization: Organization,
    identity: oidc.OIDCIdentity,
    http_request: Request,
) -> User:
    provider = f"oidc:{organization.id}"
    role = oidc.role_for_groups(identity.groups, organization.role_mappings or {})
    user = await users_repo.get_by_oauth_account(session, provider, identity.subject)

    if user is None:
        user = await users_repo.get_by_email(session, identity.email)
        if user is None:
            if not organization.allow_jit:
                raise _OAuthSignupRefused("sso_no_account")
            username = await users_repo.suggest_username(session, identity.email.split("@")[0])
            user = await users_repo.create_user(
                session,
                email=identity.email,
                username=username,
                organization_id=organization.id,
                role=role,
            )
            await users_repo.link_oauth_account(session, user, provider, identity.subject)
            await audit.record(
                session,
                audit.EVENT_SSO_PROVISIONED,
                user=user,
                organization_id=organization.id,
                ip=client_ip(http_request),
            )
        else:
            # A local account already owns an address on this tenant's domain,
            # so the tenant takes it over.
            user.organization_id = organization.id
            await session.commit()
            await users_repo.link_oauth_account(session, user, provider, identity.subject)

    if not user.is_active:
        raise _OAuthSignupRefused("account_disabled")

    # Roles are re-derived on every sign-in, so removing somebody from the IdP
    # group demotes them the next time they arrive.
    if user.role != role:
        previous = user.role
        await users_repo.set_role(session, user, role)
        await audit.record(
            session,
            audit.EVENT_ROLE_CHANGED,
            user=user,
            organization_id=organization.id,
            detail=f"{previous} -> {role} from IdP groups",
        )
    await _ensure_bootstrap_admin(session, user)
    return user


# --- consumer OAuth (Google / GitHub) ---------------------------------------


@router.get("/oauth/{provider_name}/authorize")
async def oauth_authorize(provider_name: str) -> RedirectResponse:
    provider = oauth.get_provider(provider_name)
    state = oauth.new_state()
    code_verifier = oauth.new_code_verifier()

    response = RedirectResponse(
        oauth.build_authorize_url(provider, state, code_verifier),
        status_code=status.HTTP_302_FOUND,
    )
    _set_handshake_cookie(response, _STATE_COOKIE, state)
    _set_handshake_cookie(response, _VERIFIER_COOKIE, code_verifier)
    return response


@router.get("/oauth/{provider_name}/callback")
async def oauth_callback(
    provider_name: str,
    request: Request,
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    provider = oauth.get_provider(provider_name)
    expected_state = request.cookies.get(_STATE_COOKIE)
    code_verifier = request.cookies.get(_VERIFIER_COOKIE) or ""

    if error or not code or not state or not expected_state:
        return await _oauth_failure(session, request, "oauth_failed")
    # A callback whose state we did not issue is either a stale tab or a forged
    # request trying to bind the victim's browser to the attacker's account.
    if not secrets.compare_digest(state, expected_state):
        logger.warning("Rejected %s callback with unexpected state", provider.name)
        return await _oauth_failure(session, request, "oauth_failed")

    try:
        access_token = await oauth.exchange_code(provider, code, code_verifier)
        profile = await oauth.fetch_profile(provider, access_token)
    except oauth.OAuthError as exc:
        logger.warning("OAuth handshake failed for %s: %s", provider.name, exc)
        return await _oauth_failure(session, request, "oauth_failed")

    try:
        user = await _resolve_oauth_user(session, profile)
    except _OAuthSignupRefused as exc:
        return await _oauth_failure(session, request, exc.code)
    except IntegrityError:
        await session.rollback()
        return await _oauth_failure(session, request, "oauth_failed")

    response = RedirectResponse(settings.frontend_url, status_code=status.HTTP_302_FOUND)
    await _ensure_bootstrap_admin(session, user)
    await _start_session(session, request, response, user)
    await audit.record(
        session, audit.EVENT_OAUTH_SUCCEEDED, user=user, ip=client_ip(request),
        detail=profile.provider,
    )
    _clear_handshake_cookies(response)
    return response


async def _resolve_oauth_user(session: AsyncSession, profile: oauth.OAuthProfile) -> User:
    """Find, link, or create the local account behind a provider identity."""
    user = await users_repo.get_by_oauth_account(session, profile.provider, profile.account_id)
    if user is None:
        # Everything below creates or takes over access to an account keyed by
        # email, so an address the provider has not verified is not good enough.
        if not profile.email_verified:
            logger.warning("Refused %s signup with unverified email", profile.provider)
            raise _OAuthSignupRefused("email_unverified")

        existing = await users_repo.get_by_email(session, profile.email)
        if existing is not None:
            await users_repo.link_oauth_account(
                session, existing, profile.provider, profile.account_id
            )
            user = existing
        else:
            username = await users_repo.suggest_username(session, profile.suggested_username)
            # No password hash: this account can only sign in through the provider.
            user = await users_repo.create_user(
                session, email=profile.email, username=username
            )
            await users_repo.link_oauth_account(
                session, user, profile.provider, profile.account_id
            )

    if not user.is_active:
        raise _OAuthSignupRefused("account_disabled")
    return user


# --- helpers ----------------------------------------------------------------


async def _start_session(
    session: AsyncSession, http_request: Request, response: Response, user: User
) -> SessionRecord:
    record = await sessions_repo.create_session(
        session,
        user,
        ip=client_ip(http_request),
        user_agent=http_request.headers.get("user-agent"),
    )
    token, max_age = create_session_token(record)
    set_session_cookie(response, token, max_age)
    return record


async def _ensure_bootstrap_admin(session: AsyncSession, user: User) -> None:
    """Grant the configured bootstrap address the admin role on every sign-in.

    Without it there is no way to reach the admin API on a fresh deployment.
    """
    configured = settings.bootstrap_admin_email.strip().lower()
    if configured and user.email == configured and user.role != ROLE_ADMIN:
        await users_repo.set_role(session, user, ROLE_ADMIN)
        await audit.record(
            session, audit.EVENT_ROLE_CHANGED, user=user, detail="bootstrap admin"
        )


async def _reject_if_sso_enforced(
    session: AsyncSession, http_request: Request, email: str
) -> None:
    organization = await orgs_repo.get_for_email(session, email)
    if organization is None or not organization.is_active or not organization.enforce_sso:
        return
    # Keyed on the domain, never on whether the account exists, so this stays
    # free of the account oracle that a per-user message would create.
    await audit.record(
        session,
        audit.EVENT_LOGIN_BLOCKED_SSO,
        email=email[:320],
        organization_id=organization.id,
        ip=client_ip(http_request),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This domain uses single sign-on",
    )


async def _sso_failure(
    session: AsyncSession,
    http_request: Request,
    code: str,
    detail: str,
    organization: Organization | None = None,
) -> RedirectResponse:
    await audit.record(
        session,
        audit.EVENT_SSO_FAILED,
        organization_id=organization.id if organization else None,
        ip=client_ip(http_request),
        detail=detail,
    )
    return _redirect_with_error(code)


async def _oauth_failure(
    session: AsyncSession, http_request: Request, code: str
) -> RedirectResponse:
    await audit.record(
        session, audit.EVENT_OAUTH_FAILED, ip=client_ip(http_request), detail=code
    )
    return _redirect_with_error(code)


def _redirect_with_error(code: str) -> RedirectResponse:
    # `code` is always one of our own constants, never provider or user input.
    separator = "&" if "?" in settings.frontend_url else "?"
    response = RedirectResponse(
        f"{settings.frontend_url}{separator}auth_error={code}", status_code=status.HTTP_302_FOUND
    )
    _clear_handshake_cookies(response)
    return response


def _sso_redirect_uri() -> str:
    return f"{settings.oauth_redirect_base_url.rstrip('/')}/auth/sso/callback"


def _set_handshake_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=_HANDSHAKE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        # lax, not strict: the provider sends the browser back with a top-level
        # GET, and strict would withhold the cookie exactly then.
        samesite="lax",
        path=_HANDSHAKE_PATH,
        domain=settings.cookie_domain or None,
    )


def _clear_handshake_cookies(response: Response) -> None:
    for name in _HANDSHAKE_COOKIES:
        response.delete_cookie(
            name,
            path=_HANDSHAKE_PATH,
            domain=settings.cookie_domain or None,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )


def _taken() -> HTTPException:
    # Deliberately does not say WHICH field collided.
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Email or username is already taken",
    )
