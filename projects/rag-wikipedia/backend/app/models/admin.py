from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models import ROLES, AuthEvent, Organization

_DOMAIN_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z]{2,})+$")


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    # At least one domain, or nothing would ever route to this tenant.
    domains: list[str] = Field(..., min_length=1, max_length=50)
    issuer: str = Field(..., max_length=255)
    client_id: str = Field(..., min_length=1, max_length=255)
    client_secret: str = Field(..., min_length=1, max_length=400)
    groups_claim: str = Field(default="groups", max_length=64)
    # IdP group (Entra sends object ids, Okta usually names) -> local role.
    role_mappings: dict[str, str] = Field(default_factory=dict)
    allow_jit: bool = True
    enforce_sso: bool = True

    @field_validator("issuer")
    @classmethod
    def issuer_must_be_https(cls, value: str) -> str:
        # The client secret and every token travel to this host; plain http
        # would put both on the wire in the clear.
        if not value.startswith("https://"):
            raise ValueError("issuer must be an https URL")
        return value.rstrip("/")

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        domains = []
        for entry in value:
            domain = entry.strip().lower().lstrip("@")
            if not _DOMAIN_PATTERN.match(domain):
                raise ValueError(f"{entry!r} is not a valid domain")
            domains.append(domain)
        if len(set(domains)) != len(domains):
            raise ValueError("duplicate domain")
        return domains

    @field_validator("role_mappings")
    @classmethod
    def known_roles_only(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value.values()) - set(ROLES))
        if unknown:
            raise ValueError(f"unknown role(s): {', '.join(unknown)}")
        return value


class OrganizationResponse(BaseModel):
    id: str
    name: str
    domains: list[str]
    issuer: str
    client_id: str
    groups_claim: str
    role_mappings: dict[str, str]
    allow_jit: bool
    enforce_sso: bool
    is_active: bool
    created_at: datetime

    # Note the absence of client_secret: it is write-only by design and never
    # leaves the database, not even for an administrator.
    @classmethod
    def from_organization(cls, organization: Organization) -> OrganizationResponse:
        return cls(
            id=organization.id,
            name=organization.name,
            domains=sorted(domain.domain for domain in organization.domains),
            issuer=organization.issuer,
            client_id=organization.client_id,
            groups_claim=organization.groups_claim,
            role_mappings=organization.role_mappings or {},
            allow_jit=organization.allow_jit,
            enforce_sso=organization.enforce_sso,
            is_active=organization.is_active,
            created_at=organization.created_at,
        )


class AuthEventResponse(BaseModel):
    id: str
    created_at: datetime
    event: str
    user_id: str | None
    organization_id: str | None
    email: str | None
    ip: str | None
    detail: str | None

    @classmethod
    def from_event(cls, event: AuthEvent) -> AuthEventResponse:
        return cls(
            id=event.id,
            created_at=event.created_at,
            event=event.event,
            user_id=event.user_id,
            organization_id=event.organization_id,
            email=event.email,
            ip=event.ip,
            detail=event.detail,
        )
