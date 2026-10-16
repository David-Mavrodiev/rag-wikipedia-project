"""Clerk tokens: verified, not trusted.

Real RS256 tokens, signed with a key pair this test generates, served through a
fake JWKS endpoint. Nothing reaches the network, and nothing is faked about the
part that matters - the signature check is the real one.

The cases are the ones that decide whether this is authentication or decoration:
a tampered token, an expired one, one from another issuer, one signed by a key
the issuer never published.
"""

from __future__ import annotations

import time

import pytest

jwt = pytest.importorskip("jwt", reason="PyJWT (the `auth` extra) is not installed")
pytest.importorskip("cryptography", reason="PyJWT needs cryptography for RS256")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from providers.clerk import (  # noqa: E402
    AuthError,
    ClerkVerifier,
    Principal,
    bearer_token,
    groups_from_claims,
)

ISSUER = "https://clerk.example.com"


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()

    def b64(value: int) -> str:
        import base64

        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwks = {"keys": [{"kty": "RSA", "kid": "key-1", "use": "sig", "alg": "RS256",
                      "n": b64(numbers.n), "e": b64(numbers.e)}]}
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, jwks


@pytest.fixture
def fetches(keys):
    _, jwks = keys
    calls = {"n": 0}

    def http_get(url):
        calls["n"] += 1
        assert url == f"{ISSUER}/.well-known/jwks.json"
        return jwks

    http_get.calls = calls
    return http_get


@pytest.fixture
def verifier(fetches):
    return ClerkVerifier(ISSUER, http_get=fetches)


def token_for(keys, *, kid="key-1", issuer=ISSUER, expires_in=600, **claims):
    pem, _ = keys
    payload = {"sub": "user_123", "iss": issuer, "iat": int(time.time()),
               "exp": int(time.time()) + expires_in, **claims}
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


# --- what must pass -----------------------------------------------------------

def test_a_valid_token_identifies_its_user(verifier, keys):
    principal = verifier.verify(token_for(keys))
    assert principal.signed_in and principal.user_id == "user_123"


def test_the_key_set_is_fetched_once_and_reused(verifier, keys, fetches):
    verifier.verify(token_for(keys))
    verifier.verify(token_for(keys))
    assert fetches.calls["n"] == 1


# --- what must fail -----------------------------------------------------------

def test_a_tampered_token_is_refused(verifier, keys):
    token = token_for(keys)
    head, payload, signature = token.split(".")
    with pytest.raises(AuthError):
        verifier.verify(f"{head}.{payload}x.{signature}")


def test_an_expired_token_is_refused(verifier, keys):
    with pytest.raises(AuthError):
        verifier.verify(token_for(keys, expires_in=-60))


def test_a_token_from_another_issuer_is_refused(verifier, keys):
    with pytest.raises(AuthError):
        verifier.verify(token_for(keys, issuer="https://clerk.attacker.test"))


def test_a_token_signed_by_an_unpublished_key_is_refused(verifier):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode({"sub": "u", "iss": ISSUER, "exp": int(time.time()) + 60},
                       pem, algorithm="RS256", headers={"kid": "key-1"})
    with pytest.raises(AuthError):
        verifier.verify(token)


def test_an_unknown_key_id_does_not_refetch_on_every_request(verifier, keys, fetches):
    verifier.verify(token_for(keys))  # one fetch, cache warm
    for _ in range(5):
        with pytest.raises(AuthError):
            verifier.verify(token_for(keys, kid="made-up"))
    # A prober sending random key ids must not turn us into a request amplifier.
    assert fetches.calls["n"] == 1


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "..."])
def test_rubbish_is_refused_without_an_exception_escaping(verifier, token):
    with pytest.raises(AuthError):
        verifier.verify(token)


# --- groups -------------------------------------------------------------------

def test_everyone_signed_in_can_still_read_the_public_corpus():
    assert "public" in groups_from_claims({"sub": "u"})


def test_explicit_groups_come_through_in_either_shape():
    assert "legal" in groups_from_claims({"groups": ["legal"]})
    assert "legal" in groups_from_claims({"groups": "legal, research"})


def test_an_organisation_role_becomes_a_group():
    groups = groups_from_claims({"org_role": "org:admin", "org_slug": "acme"})
    assert "org-admin" in groups and "acme" in groups


def test_groups_are_sorted_and_unique_so_a_filter_is_stable():
    assert groups_from_claims({"groups": ["b", "a", "b"]}) == ("a", "b", "public")


# --- the header ---------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"),
    ("bearer abc", "abc"),
    ("Basic abc", None),
    ("Bearer   ", None),
    ("abc", None),
    (None, None),
])
def test_bearer_tokens_are_read_leniently_and_nothing_else_is(header, expected):
    assert bearer_token(header) == expected


def test_an_anonymous_principal_is_a_real_answer_not_an_error():
    assert Principal.anonymous().signed_in is False
