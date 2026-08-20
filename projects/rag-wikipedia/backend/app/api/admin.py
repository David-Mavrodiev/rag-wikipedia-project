"""Administrator-only endpoints, gated on the admin role.

Roles come from the IdP: an organization maps its groups to roles, so who can
reach these routes is managed where the rest of enterprise access already is.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.core.security import client_ip, require_admin
from app.db import audit
from app.db import organizations as orgs_repo
from app.db import sessions as sessions_repo
from app.db import users as users_repo
from app.db.models import User
from app.db.session import get_session
from app.models.admin import AuthEventResponse, OrganizationCreate, OrganizationResponse
from app.models.auth import UserResponse

logger = logging.getLogger(__name__)
# The router-level dependency is the guarantee: every route added here is
# admin-only by default rather than by remembering to annotate it.
router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = Annotated[User, Depends(require_admin)]


@router.post(
    "/organizations", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    payload: OrganizationCreate, admin: AdminUser, request: Request, session: SessionDep
) -> OrganizationResponse:
    for domain in payload.domains:
        if await orgs_repo.get_by_domain(session, domain) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Domain {domain} already belongs to an organization",
            )

    try:
        organization = await orgs_repo.create(
            session,
            name=payload.name,
            domains=payload.domains,
            issuer=payload.issuer,
            client_id=payload.client_id,
            # Encrypted before it touches the database; the plaintext exists
            # only for the length of this request.
            client_secret_encrypted=encrypt_secret(payload.client_secret),
            groups_claim=payload.groups_claim,
            role_mappings=payload.role_mappings,
            allow_jit=payload.allow_jit,
            enforce_sso=payload.enforce_sso,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization name or domain is already taken",
        ) from exc

    await audit.record(
        session,
        audit.EVENT_ORGANIZATION_CREATED,
        user=admin,
        organization_id=organization.id,
        ip=client_ip(request),
        detail=f"{organization.name}: {', '.join(payload.domains)}",
    )
    return OrganizationResponse.from_organization(organization)


@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_organizations(session: SessionDep) -> list[OrganizationResponse]:
    return [
        OrganizationResponse.from_organization(organization)
        for organization in await orgs_repo.list_all(session)
    ]


@router.get("/audit-events", response_model=list[AuthEventResponse])
async def list_audit_events(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuthEventResponse]:
    events = await audit.list_events(session, limit=limit, offset=offset)
    return [AuthEventResponse.from_event(event) for event in events]


@router.post("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: str, admin: AdminUser, request: Request, session: SessionDep
) -> UserResponse:
    user = await users_repo.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        # Locking the last administrator out of their own console helps nobody.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    await users_repo.deactivate(session, user)
    # Deactivation has to bite now, not when the last token happens to expire.
    revoked = await sessions_repo.revoke_all_for_user(session, user.id)
    await audit.record(
        session,
        audit.EVENT_USER_DEACTIVATED,
        user=user,
        ip=client_ip(request),
        detail=f"deactivated by {admin.email}; {revoked} session(s) revoked",
    )
    return UserResponse.from_user(user)
