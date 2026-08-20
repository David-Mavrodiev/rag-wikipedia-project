from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, OrganizationDomain


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().lstrip("@")


def domain_of(email: str) -> str:
    """The part of an address that decides which tenant owns the sign-in."""
    return email.strip().lower().rpartition("@")[2]


async def get_by_id(session: AsyncSession, organization_id: str) -> Organization | None:
    return await session.get(Organization, organization_id)


async def get_by_domain(session: AsyncSession, domain: str) -> Organization | None:
    result = await session.execute(
        select(Organization)
        .join(OrganizationDomain)
        .where(OrganizationDomain.domain == normalize_domain(domain))
    )
    return result.scalar_one_or_none()


async def get_for_email(session: AsyncSession, email: str) -> Organization | None:
    domain = domain_of(email)
    if not domain:
        return None
    return await get_by_domain(session, domain)


async def list_all(session: AsyncSession) -> list[Organization]:
    result = await session.execute(select(Organization).order_by(Organization.name))
    return list(result.scalars().all())


async def create(
    session: AsyncSession,
    *,
    name: str,
    domains: list[str],
    issuer: str,
    client_id: str,
    client_secret_encrypted: str,
    groups_claim: str,
    role_mappings: dict[str, str],
    allow_jit: bool,
    enforce_sso: bool,
) -> Organization:
    organization = Organization(
        name=name,
        issuer=issuer.rstrip("/"),
        client_id=client_id,
        client_secret_encrypted=client_secret_encrypted,
        groups_claim=groups_claim,
        role_mappings=role_mappings,
        allow_jit=allow_jit,
        enforce_sso=enforce_sso,
        domains=[OrganizationDomain(domain=normalize_domain(domain)) for domain in domains],
    )
    session.add(organization)
    await session.commit()
    await session.refresh(organization)
    return organization


def owns_domain(organization: Organization, email: str) -> bool:
    """Whether an address belongs to a domain this tenant has registered.

    This is what makes the IdP's word on an email trustworthy: it is authoritative
    for the domains its organization owns, and for nothing else.
    """
    return domain_of(email) in {domain.domain for domain in organization.domains}
