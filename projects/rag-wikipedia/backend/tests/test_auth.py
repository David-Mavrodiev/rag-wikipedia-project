from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from app.core import security

# --- password hashing -------------------------------------------------------


def test_hash_password_is_salted():
    # Two hashes of the same password must differ: bcrypt draws a fresh random
    # salt each time, so one stolen table cannot be cracked with one rainbow set.
    first = security.hash_password("s3cret-password")
    second = security.hash_password("s3cret-password")

    assert first != second
    assert "s3cret-password" not in first
    assert security.verify_password("s3cret-password", first) is True
    assert security.verify_password("s3cret-password", second) is True
    assert security.verify_password("wrong", first) is False


def test_hash_password_rejects_overlong_password():
    with pytest.raises(ValueError):
        security.hash_password("x" * (security.MAX_PASSWORD_BYTES + 1))


def test_verify_password_fails_closed_on_malformed_hash():
    assert security.verify_password("anything", "not-a-bcrypt-hash") is False


# --- registration -----------------------------------------------------------


def test_register_creates_an_account_and_signs_it_in(anon_client, credentials):
    response = anon_client.post("/auth/register", json=credentials)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == credentials["email"]
    assert body["username"] == credentials["username"]
    assert body["has_password"] is True
    assert body["providers"] == []
    # Neither the password nor the raw token is ever echoed back.
    assert credentials["password"] not in response.text
    assert "access_token" not in response.text
    assert anon_client.get("/auth/me").status_code == 200


def test_register_sets_an_httponly_session_cookie(anon_client, credentials):
    response = anon_client.post("/auth/register", json=credentials)

    cookie = response.headers["set-cookie"].lower()
    assert "rag_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


def test_register_rejects_a_duplicate_email(anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    response = anon_client.post(
        "/auth/register", json={**credentials, "username": "someone.else"}
    )
    assert response.status_code == 409


def test_register_rejects_a_duplicate_username(anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    response = anon_client.post(
        "/auth/register", json={**credentials, "email": "other@example.com"}
    )
    assert response.status_code == 409


def test_register_conflict_does_not_say_which_field_collided(anon_client, credentials):
    anon_client.post("/auth/register", json=credentials)
    email_taken = anon_client.post(
        "/auth/register", json={**credentials, "username": "someone.else"}
    )
    username_taken = anon_client.post(
        "/auth/register", json={**credentials, "email": "other@example.com"}
    )
    assert email_taken.json()["detail"] == username_taken.json()["detail"]


@pytest.mark.parametrize(
    "override",
    [
        {"email": "not-an-email"},
        {"username": "ab"},
        {"username": "has spaces"},
        {"username": "inject'; drop table users; --"},
        {"password": "short"},
    ],
)
def test_register_validates_input(anon_client, credentials, override):
    response = anon_client.post("/auth/register", json={**credentials, **override})
    assert response.status_code == 422


def test_register_rejects_a_password_over_the_bcrypt_byte_limit(anon_client, credentials):
    # 40 characters but 80 bytes: inside max_length, past what bcrypt hashes.
    response = anon_client.post("/auth/register", json={**credentials, "password": "é" * 40})
    assert response.status_code == 422


def test_email_is_matched_case_insensitively(anon_client, credentials):
    anon_client.post("/auth/register", json={**credentials, "email": "Tester@Example.COM"})
    anon_client.cookies.clear()

    response = anon_client.post(
        "/auth/login",
        json={"identifier": "tester@example.com", "password": credentials["password"]},
    )
    assert response.status_code == 200


# --- login / logout ---------------------------------------------------------


def test_login_with_email(registered_client, credentials):
    response = registered_client.post(
        "/auth/login",
        json={"identifier": credentials["email"], "password": credentials["password"]},
    )

    assert response.status_code == 200
    assert registered_client.get("/auth/me").json()["username"] == credentials["username"]


def test_login_with_username(registered_client, credentials):
    response = registered_client.post(
        "/auth/login",
        json={"identifier": credentials["username"], "password": credentials["password"]},
    )

    assert response.status_code == 200
    # The token lives in the cookie only, so a script on the page cannot read it.
    assert "access_token" not in response.text


def test_login_wrong_password_is_401(registered_client, credentials):
    response = registered_client.post(
        "/auth/login", json={"identifier": credentials["email"], "password": "nope"}
    )

    assert response.status_code == 401
    assert registered_client.get("/auth/me").status_code == 401


def test_login_unknown_account_is_401(registered_client):
    response = registered_client.post(
        "/auth/login", json={"identifier": "ghost@example.com", "password": "whatever"}
    )
    assert response.status_code == 401


def test_login_error_does_not_reveal_whether_the_account_exists(registered_client, credentials):
    unknown = registered_client.post(
        "/auth/login", json={"identifier": "ghost@example.com", "password": "nope"}
    )
    wrong_password = registered_client.post(
        "/auth/login", json={"identifier": credentials["email"], "password": "nope"}
    )
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_logout_clears_the_session(client):
    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert client.get("/auth/me").status_code == 401


# --- protected endpoints ----------------------------------------------------


def test_me_requires_authentication(anon_client):
    assert anon_client.get("/auth/me").status_code == 401


def test_health_stays_public(anon_client):
    # The container healthcheck has no credentials.
    assert anon_client.get("/health").status_code == 200


def test_query_requires_authentication(anon_client):
    response = anon_client.post("/query", json={"question": "What is Python?"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_query_accepts_the_session_cookie(client):
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = [
            {"score": 0.95, "text": "Python is a language.", "title": "Python", "source_id": "1"}
        ]
        mock_llm.return_value.generate.return_value = "Python is a language [1]."
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 200
    assert response.json()["refused"] is False


def test_query_accepts_a_bearer_token(client):
    # Fallback for scripts and curl, which have no cookie jar: the same signed
    # session value, presented as a header instead.
    token = client.cookies.get("rag_session")
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token}"

    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm"),
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = []
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 200


def test_query_rejects_a_malformed_token(anon_client):
    anon_client.headers["Authorization"] = "Bearer not-a-token"
    assert anon_client.post("/query", json={"question": "What is Python?"}).status_code == 401


def test_query_rejects_a_token_signed_with_another_key(client):
    user_id = client.get("/auth/me").json()["id"]
    client.cookies.clear()
    forged = jwt.encode({"sub": user_id, "sid": "forged"}, "attacker-key", algorithm="HS256")
    client.headers["Authorization"] = f"Bearer {forged}"

    assert client.post("/query", json={"question": "What is Python?"}).status_code == 401


def test_query_rejects_a_token_signed_with_another_algorithm(client, jwt_secret):
    # Same secret, different alg: verification must not follow the token's own
    # header when choosing how to check the signature.
    user_id = client.get("/auth/me").json()["id"]
    client.cookies.clear()
    forged = jwt.encode({"sub": user_id, "sid": "forged"}, jwt_secret, algorithm="HS512")
    client.headers["Authorization"] = f"Bearer {forged}"

    assert client.post("/query", json={"question": "What is Python?"}).status_code == 401


def test_query_rejects_an_expired_token(anon_client, jwt_secret):
    expired = jwt.encode(
        {"sub": "any-user", "sid": "any-session", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        jwt_secret,
        algorithm="HS256",
    )
    anon_client.headers["Authorization"] = f"Bearer {expired}"
    response = anon_client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Session expired"


def test_query_rejects_a_token_for_an_unknown_session(anon_client, jwt_secret):
    # Well-signed and unexpired, but it points at no session row — which is what
    # a token minted before the session was deleted looks like.
    orphan = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "sid": "00000000-0000-0000-0000-000000000001",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        jwt_secret,
        algorithm="HS256",
    )
    anon_client.headers["Authorization"] = f"Bearer {orphan}"

    assert anon_client.post("/query", json={"question": "What is Python?"}).status_code == 401


def test_auth_is_checked_before_body_validation(anon_client):
    # An anonymous caller gets 401, not 422: the service should not report on
    # the shape of a request it never intends to process.
    assert anon_client.post("/query", json={"question": ""}).status_code == 401
