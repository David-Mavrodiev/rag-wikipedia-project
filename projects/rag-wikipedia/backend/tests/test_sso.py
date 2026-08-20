"""Enterprise OIDC single sign-on: token verification and the tenant flow."""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from app.core import oidc
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.acme.example"
JWKS_URI = f"{ISSUER}/keys"
CLIENT_ID = "acme-client"
CLIENT_SECRET = "acme-client-secret"
KID = "test-key"
LOGIN_EMAIL = "alice@acme.example"

DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": JWKS_URI,
}

ORG_PAYLOAD = {
    "name": "Acme",
    "domains": ["acme.example"],
    "issuer": ISSUER,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "groups_claim": "groups",
    "role_mappings": {"acme-admins": "admin"},
}


@pytest.fixture(scope="module")
def signing_key():
    # Module-scoped: RSA key generation is the slowest thing in this file.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def provider_endpoints(signing_key):
    """Serve the IdP's discovery document and key set without a network."""

    async def fake_get_json(url: str):
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return DISCOVERY
        if url == JWKS_URI:
            return jwks_document(signing_key)
        raise AssertionError(f"unexpected provider request: {url}")

    with patch("app.core.oidc._get_json", new=fake_get_json):
        yield


@pytest.fixture
def make_organization(admin_client):
    def _create(**overrides):
        response = admin_client.post("/admin/organizations", json={**ORG_PAYLOAD, **overrides})
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def organization(make_organization):
    return make_organization()


def base64url(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def jwks_document(key) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": base64url(numbers.n),
                "e": base64url(numbers.e),
            }
        ]
    }


def id_token(signing_key, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-user-1",
        "email": LOGIN_EMAIL,
        "name": "Alice Example",
        "groups": ["acme-admins"],
        "nonce": "test-nonce",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": KID})


def sso_callback(client, signing_key, *, login_email=LOGIN_EMAIL, state=None, **claims):
    """Run a full handshake: start, then return from the provider with a token."""
    start = client.post("/auth/sso/start", json={"email": login_email})
    assert start.status_code == 200, start.text
    claims.setdefault("email", login_email)
    token = id_token(signing_key, nonce=client.cookies.get("oidc_nonce"), **claims)

    with patch("app.core.oidc.exchange_code", AsyncMock(return_value=token)):
        return client.get(
            "/auth/sso/callback",
            params={"code": "auth-code", "state": state or client.cookies.get("oauth_state")},
            follow_redirects=False,
        )


# --- id_token verification --------------------------------------------------


async def test_verify_accepts_a_correctly_signed_token(signing_key):
    metadata = await oidc.get_metadata(ISSUER)

    claims = await oidc.verify_id_token(
        id_token(signing_key), metadata, client_id=CLIENT_ID, nonce="test-nonce"
    )
    assert claims["sub"] == "idp-user-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-client"},
        {"iss": "https://idp.evil.example"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"sub": None},
    ],
)
async def test_verify_rejects_tokens_that_fail_a_required_claim(signing_key, overrides):
    metadata = await oidc.get_metadata(ISSUER)

    with pytest.raises(oidc.OIDCError):
        await oidc.verify_id_token(
            id_token(signing_key, **overrides), metadata, client_id=CLIENT_ID, nonce="test-nonce"
        )


async def test_verify_rejects_a_replayed_nonce(signing_key):
    metadata = await oidc.get_metadata(ISSUER)

    # A token minted for a different sign-in attempt must not be usable here.
    with pytest.raises(oidc.OIDCError):
        await oidc.verify_id_token(
            id_token(signing_key, nonce="somebody-elses-nonce"),
            metadata,
            client_id=CLIENT_ID,
            nonce="test-nonce",
        )


async def test_verify_rejects_a_token_signed_with_the_client_secret():
    # The classic OIDC confusion: the client secret is shared configuration, so
    # an HS256 token signed with it must never be accepted as the IdP's word.
    metadata = await oidc.get_metadata(ISSUER)
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "attacker",
            "email": "attacker@acme.example",
            "nonce": "test-nonce",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        CLIENT_SECRET,
        algorithm="HS256",
        headers={"kid": KID},
    )

    with pytest.raises(oidc.OIDCError):
        await oidc.verify_id_token(forged, metadata, client_id=CLIENT_ID, nonce="test-nonce")


async def test_discovery_rejects_a_document_for_another_issuer():
    async def impostor(url: str):
        return {**DISCOVERY, "issuer": "https://idp.evil.example"}

    with patch("app.core.oidc._get_json", new=impostor), pytest.raises(oidc.OIDCError):
        await oidc.get_metadata(ISSUER)


def test_role_mapping_prefers_the_strongest_match():
    mappings = {"acme-admins": "admin", "acme-staff": "member"}
    assert oidc.role_for_groups(["acme-staff", "acme-admins"], mappings) == "admin"
    assert oidc.role_for_groups(["acme-staff"], mappings) == "member"
    assert oidc.role_for_groups(["unmapped"], mappings) == "member"


# --- routing ----------------------------------------------------------------


def test_start_routes_the_domain_to_its_provider(anon_client, organization):
    response = anon_client.post("/auth/sso/start", json={"email": LOGIN_EMAIL})

    assert response.status_code == 200
    url = urlparse(response.json()["authorize_url"])
    params = parse_qs(url.query)
    assert url.netloc == "idp.acme.example"
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == ["http://api.test/auth/sso/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [anon_client.cookies.get("oauth_state")]
    assert params["nonce"] == [anon_client.cookies.get("oidc_nonce")]


def test_start_on_an_unmanaged_domain_is_404(anon_client, organization):
    response = anon_client.post("/auth/sso/start", json={"email": "someone@gmail.com"})
    assert response.status_code == 404


# --- sign-in ----------------------------------------------------------------


def test_callback_provisions_the_account_on_first_sign_in(anon_client, organization, signing_key):
    response = sso_callback(anon_client, signing_key)

    assert response.status_code == 302
    assert response.headers["location"] == "http://frontend.test"
    me = anon_client.get("/auth/me").json()
    assert me["email"] == LOGIN_EMAIL
    assert me["organization"] == "Acme"
    assert me["has_password"] is False
    # "acme-admins" is mapped to admin by the organization.
    assert me["role"] == "admin"


def test_second_sign_in_reuses_the_account(anon_client, organization, signing_key):
    sso_callback(anon_client, signing_key)
    first_id = anon_client.get("/auth/me").json()["id"]
    anon_client.cookies.clear()

    sso_callback(anon_client, signing_key)
    assert anon_client.get("/auth/me").json()["id"] == first_id


def test_roles_follow_the_idp_on_every_sign_in(anon_client, organization, signing_key):
    sso_callback(anon_client, signing_key)
    assert anon_client.get("/auth/me").json()["role"] == "admin"
    anon_client.cookies.clear()

    # Removed from the admin group at the provider.
    sso_callback(anon_client, signing_key, groups=["acme-staff"])
    assert anon_client.get("/auth/me").json()["role"] == "member"


def test_callback_refuses_an_email_outside_the_tenant(anon_client, organization, signing_key):
    # The provider is trusted for the domains its organization registered, and
    # for nothing else — otherwise one tenant could assert any address.
    response = sso_callback(anon_client, signing_key, email="alice@other.example")

    assert "auth_error=sso_domain_mismatch" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_rejects_a_state_we_did_not_issue(anon_client, organization, signing_key):
    response = sso_callback(anon_client, signing_key, state="attacker-supplied-state")

    assert "auth_error=sso_failed" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_callback_without_jit_refuses_an_unknown_user(anon_client, make_organization, signing_key):
    make_organization(allow_jit=False)

    response = sso_callback(anon_client, signing_key)

    assert "auth_error=sso_no_account" in response.headers["location"]
    assert anon_client.get("/auth/me").status_code == 401


def test_sso_adopts_an_existing_local_account(anon_client, make_organization, signing_key):
    # A tenant that still allows passwords, so the account can exist first.
    make_organization(enforce_sso=False)
    anon_client.post(
        "/auth/register",
        json={"email": LOGIN_EMAIL, "username": "alice", "password": "a-good-password"},
    )
    local_id = anon_client.get("/auth/me").json()["id"]
    anon_client.cookies.clear()

    sso_callback(anon_client, signing_key)

    me = anon_client.get("/auth/me").json()
    assert me["id"] == local_id
    assert me["organization"] == "Acme"
    assert me["has_password"] is True  # the original password still works


# --- SSO enforcement --------------------------------------------------------


def test_password_registration_is_blocked_on_a_managed_domain(anon_client, organization):
    response = anon_client.post(
        "/auth/register",
        json={"email": LOGIN_EMAIL, "username": "alice", "password": "a-good-password"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "This domain uses single sign-on"


def test_password_login_is_blocked_on_a_managed_domain(anon_client, organization):
    response = anon_client.post(
        "/auth/login", json={"identifier": LOGIN_EMAIL, "password": "anything"}
    )
    assert response.status_code == 403


def test_enforcement_is_decided_by_domain_not_by_account(anon_client, organization):
    # Both answers are identical, so this cannot be used to discover which
    # addresses at a managed domain are real accounts.
    existing = anon_client.post(
        "/auth/login", json={"identifier": LOGIN_EMAIL, "password": "anything"}
    )
    unknown = anon_client.post(
        "/auth/login", json={"identifier": "nobody@acme.example", "password": "anything"}
    )
    assert existing.status_code == unknown.status_code == 403
    assert existing.json() == unknown.json()


def test_passwords_still_work_when_a_tenant_does_not_enforce_sso(anon_client, make_organization):
    make_organization(enforce_sso=False)

    response = anon_client.post(
        "/auth/register",
        json={"email": LOGIN_EMAIL, "username": "alice", "password": "a-good-password"},
    )
    assert response.status_code == 201
