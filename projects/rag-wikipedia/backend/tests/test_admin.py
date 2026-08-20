"""Admin surface: RBAC enforcement, tenant registration, audit trail."""

import pytest
from app.core import crypto
from app.core.config import settings
from cryptography.fernet import Fernet

ORG_PAYLOAD = {
    "name": "Acme",
    "domains": ["acme.example", "acme-labs.example"],
    "issuer": "https://idp.acme.example",
    "client_id": "acme-client",
    "client_secret": "acme-client-secret",
    "role_mappings": {"acme-admins": "admin"},
}

ADMIN_ROUTES = [
    ("get", "/admin/organizations"),
    ("get", "/admin/audit-events"),
]


# --- access control ---------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_reject_anonymous_callers(anon_client, method, path):
    assert getattr(anon_client, method)(path).status_code == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_reject_members(client, method, path):
    # `client` is a signed-in ordinary account.
    assert getattr(client, method)(path).status_code == 403


def test_member_cannot_register_an_organization(client):
    assert client.post("/admin/organizations", json=ORG_PAYLOAD).status_code == 403


def test_admin_reaches_the_admin_surface(admin_client):
    assert admin_client.get("/admin/organizations").status_code == 200


# --- organizations ----------------------------------------------------------


def test_create_organization(admin_client):
    response = admin_client.post("/admin/organizations", json=ORG_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["domains"] == ["acme-labs.example", "acme.example"]
    assert body["enforce_sso"] is True
    assert body["allow_jit"] is True


def test_organization_never_returns_the_client_secret(admin_client):
    created = admin_client.post("/admin/organizations", json=ORG_PAYLOAD)
    listed = admin_client.get("/admin/organizations")

    assert "client_secret" not in created.text
    assert ORG_PAYLOAD["client_secret"] not in created.text
    assert ORG_PAYLOAD["client_secret"] not in listed.text


def test_a_domain_belongs_to_one_tenant_only(admin_client):
    admin_client.post("/admin/organizations", json=ORG_PAYLOAD)

    response = admin_client.post(
        "/admin/organizations",
        json={**ORG_PAYLOAD, "name": "Acme Subsidiary", "domains": ["acme.example"]},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "override",
    [
        {"issuer": "http://idp.acme.example"},  # cleartext
        {"issuer": "idp.acme.example"},  # no scheme
        {"domains": []},
        {"domains": ["not a domain"]},
        {"role_mappings": {"acme-admins": "superuser"}},
    ],
)
def test_organization_input_is_validated(admin_client, override):
    response = admin_client.post("/admin/organizations", json={**ORG_PAYLOAD, **override})
    assert response.status_code == 422


# --- secret storage ---------------------------------------------------------


def test_client_secret_roundtrips_through_encryption():
    ciphertext = crypto.encrypt_secret("acme-client-secret")

    assert "acme-client-secret" not in ciphertext
    assert crypto.decrypt_secret(ciphertext) == "acme-client-secret"


def test_a_secret_cannot_be_read_with_a_different_key(monkeypatch):
    ciphertext = crypto.encrypt_secret("acme-client-secret")
    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())

    with pytest.raises(Exception) as error:
        crypto.decrypt_secret(ciphertext)
    assert error.value.status_code == 503


def test_registering_a_tenant_fails_closed_without_an_encryption_key(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "secret_encryption_key", "")

    response = admin_client.post("/admin/organizations", json=ORG_PAYLOAD)
    assert response.status_code == 503


# --- audit trail ------------------------------------------------------------


def test_audit_trail_records_sign_in_and_failure(admin_client, anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    anon_client.cookies.clear()
    anon_client.post(
        "/auth/login", json={"identifier": credentials["email"], "password": "wrong"}
    )

    events = admin_client.get("/admin/audit-events").json()

    recorded = {event["event"] for event in events}
    assert "user.registered" in recorded
    assert "login.password.failed" in recorded
    failure = next(event for event in events if event["event"] == "login.password.failed")
    assert failure["email"] == credentials["email"]
    # A failed attempt is attributable even though no session was created.
    assert failure["user_id"] is None


def test_audit_trail_never_stores_the_password(admin_client, anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)

    assert credentials["password"] not in admin_client.get("/admin/audit-events").text


def test_audit_trail_is_paginated(admin_client, anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)

    page = admin_client.get("/admin/audit-events", params={"limit": 1}).json()
    assert len(page) == 1
    assert admin_client.get("/admin/audit-events", params={"limit": 0}).status_code == 422


# --- deactivation -----------------------------------------------------------


def test_deactivate_a_user(admin_client, anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    user_id = anon_client.get("/auth/me").json()["id"]

    response = admin_client.post(f"/admin/users/{user_id}/deactivate")

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_deactivation_is_recorded(admin_client, anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    user_id = anon_client.get("/auth/me").json()["id"]
    admin_client.post(f"/admin/users/{user_id}/deactivate")

    events = admin_client.get("/admin/audit-events").json()
    assert any(event["event"] == "user.deactivated" for event in events)


def test_an_admin_cannot_lock_themselves_out(admin_client):
    admin_id = admin_client.get("/auth/me").json()["id"]

    response = admin_client.post(f"/admin/users/{admin_id}/deactivate")
    assert response.status_code == 400


def test_deactivating_an_unknown_user_is_404(admin_client):
    assert admin_client.post("/admin/users/does-not-exist/deactivate").status_code == 404
