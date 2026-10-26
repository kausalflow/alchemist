"""ALC_AUTH=oidc: ID tokens from any OIDC provider, forwarded by a sign-in proxy, checked against the provider's keys."""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from alchemist.api import identity
from alchemist.api.identity import NotSignedIn, verify_oidc_jwt

ISSUER = "https://login.example.com/realms/plant"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def num(n: int, size: int | None = None) -> str:
    return b64(n.to_bytes(size or (n.bit_length() + 7) // 8, "big"))


def jwks() -> dict:
    r, e = RSA_KEY.public_key().public_numbers(), EC_KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "r1", "alg": "RS256", "n": num(r.n), "e": num(r.e)},
                     {"kty": "EC", "kid": "e1", "crv": "P-256", "x": num(e.x, 32), "y": num(e.y, 32)}]}


def token(claims: dict, alg: str = "RS256", kid: str = "r1") -> str:
    h = b64(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    p = b64(json.dumps(claims).encode())
    signed = f"{h}.{p}".encode()
    if alg == "RS256":
        sig = RSA_KEY.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    else:
        r, s = decode_dss_signature(EC_KEY.sign(signed, ec.ECDSA(hashes.SHA256())))
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{h}.{p}.{b64(sig)}"


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    calls = []

    def fake(url):
        calls.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"}
        return jwks()

    monkeypatch.setattr(identity, "_http_json", fake)
    monkeypatch.setattr(identity, "_jwks_cache", {})
    monkeypatch.setenv("ALC_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("ALC_OIDC_AUDIENCE", "alchemist")
    monkeypatch.delenv("ALC_OIDC_JWKS_URL", raising=False)
    return calls


def claims(**over):
    return {"iss": ISSUER, "aud": "alchemist", "sub": "u1", "email": "ana@example.com",
            "groups": ["alc-plant-P01", "alc-ws-quality"], "exp": time.time() + 300, **over}


def test_rsa_and_ec_tokens_are_accepted(provider):
    assert verify_oidc_jwt(token(claims()), key_loader=identity._http_json)["email"] == "ana@example.com"
    assert verify_oidc_jwt(token(claims(aud=["other", "alchemist"]), "ES256", "e1"),
                           key_loader=identity._http_json)["sub"] == "u1"
    assert provider[0].endswith("/.well-known/openid-configuration")


@pytest.mark.parametrize("bad,match", [
    (lambda: token(claims(aud="someone-else")), "another application"),
    (lambda: token(claims(iss="https://evil.example.com")), "another issuer"),
    (lambda: token(claims(exp=time.time() - 3600)), "expired"),
    (lambda: token(claims(nbf=time.time() + 3600)), "not valid yet"),
    (lambda: token(claims())[:-6] + "AAAAAA", "signature"),
    (lambda: token(claims(), kid="unknown"), "unknown key"),
    (lambda: token(claims(), "ES256", "r1"), "does not match"),
    (lambda: "not-a-jwt", "malformed"),
])
def test_bad_tokens_are_refused(bad, match):
    with pytest.raises(NotSignedIn, match=match):
        verify_oidc_jwt(bad(), key_loader=identity._http_json)


def test_none_algorithm_is_refused():
    h = b64(json.dumps({"alg": "none"}).encode())
    with pytest.raises(NotSignedIn, match="algorithm"):
        verify_oidc_jwt(f"{h}.{b64(json.dumps(claims()).encode())}.")


def test_audience_is_required(monkeypatch):
    monkeypatch.delenv("ALC_OIDC_AUDIENCE")
    with pytest.raises(NotSignedIn, match="ALC_OIDC_AUDIENCE"):
        verify_oidc_jwt(token(claims()), key_loader=identity._http_json)


def test_resolve_maps_groups_from_the_bearer_token(svc, monkeypatch):
    monkeypatch.setenv("ALC_AUTH", "oidc")
    p = identity.resolve(svc, {"authorization": f"Bearer {token(claims())}"}, {})
    assert p.email == "ana@example.com" and "P01" in p.user.plants and "quality" in p.user.workspaces
    with pytest.raises(NotSignedIn):
        identity.resolve(svc, {"x-alchemist-user": "data_admin"}, {})  # the dev header means nothing here
    monkeypatch.setenv("ALC_OIDC_TOKEN_HEADER", "X-Forwarded-Id-Token")
    assert identity.resolve(svc, {"x-forwarded-id-token": token(claims())}, {}).email == "ana@example.com"
