"""Who is calling: the signed-in user and what they may see.

Two modes, chosen by ALC_AUTH:

    dev       (default) the user id comes from the `X-Alchemist-User` header or the `alc_user` cookie and is looked
              up in access.yaml. For laptops and tests only: anyone can claim any user.
    alb-oidc  the corporate identity provider signs users in at the load balancer, which passes the claims in the
              signed `x-amzn-oidc-data` header. The signature, signer (the ALB's ARN) and expiry are checked, and
              group claims are mapped to plants and workspaces by the naming convention in the technical design:
              alc-plant-<code>, alc-global, alc-ws-<area>, alc-catalog-admin, alc-admin.

    oidc      any OIDC identity provider (Entra ID, Okta, Keycloak, Google, ...) outside AWS. A sign-in proxy in front
              of the API (oauth2-proxy, or the ingress's auth) signs users in and forwards their ID token as
              `Authorization: Bearer <jwt>` (or in ALC_OIDC_TOKEN_HEADER). The token's signature is checked against
              the provider's published keys (ALC_OIDC_ISSUER's discovery document, or ALC_OIDC_JWKS_URL), as are its
              issuer, audience (ALC_OIDC_AUDIENCE, the client id) and expiry. Groups map as in alb-oidc.

Either way the result is an `alchemist.access.User`, so every rule in access.py applies unchanged.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache

from ..access import AccessDenied, User


class NotSignedIn(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    user: User
    is_catalog_admin: bool
    email: str | None = None
    name: str | None = None


def auth_mode() -> str:
    return os.environ.get("ALC_AUTH", "dev")


def resolve(svc, headers, cookies) -> Principal:
    if auth_mode() == "alb-oidc":
        claims = verify_alb_jwt(headers.get("x-amzn-oidc-data", ""))
        return principal_from_claims(svc, claims)
    if auth_mode() == "oidc":
        header = os.environ.get("ALC_OIDC_TOKEN_HEADER", "authorization").lower()
        token = (headers.get(header) or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return principal_from_claims(svc, verify_oidc_jwt(token))
    user_id = headers.get("x-alchemist-user") or cookies.get("alc_user")
    if not user_id:
        raise NotSignedIn("pick a user (dev mode): set the X-Alchemist-User header or the alc_user cookie")
    try:
        user = svc.access.user(user_id)
    except AccessDenied as e:
        raise NotSignedIn(str(e)) from e
    raw = getattr(svc.access, "data", {}).get("users", {}).get(user_id, {})
    return Principal(user=user, is_catalog_admin=user.is_admin or bool(raw.get("catalog_admin"))
                     or bool(getattr(user, "is_catalog_admin", False)), name=user_id)


# ------------------------------------------------------------------ ALB OIDC

def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@lru_cache(maxsize=16)
def _alb_public_key(region: str, kid: str) -> bytes:
    import httpx

    url = f"https://public-keys.auth.elb.{region}.amazonaws.com/{kid}"
    r = httpx.get(url, timeout=5)
    r.raise_for_status()
    return r.content


def verify_alb_jwt(token: str, *, key_loader=_alb_public_key) -> dict:
    """Check the ES256 token the ALB adds after OIDC sign-in and return its claims."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        h64, p64, s64 = token.split(".")
        header, claims, sig = json.loads(_b64(h64)), json.loads(_b64(p64)), _b64(s64)
    except ValueError as e:
        raise NotSignedIn("missing or malformed x-amzn-oidc-data") from e
    expected_arn = os.environ.get("ALC_ALB_ARN")
    if not expected_arn or header.get("signer") != expected_arn:
        raise NotSignedIn("token was not signed by this load balancer (check ALC_ALB_ARN)")
    if header.get("alg") != "ES256":
        raise NotSignedIn("unexpected token algorithm")
    region = os.environ.get("AWS_REGION", "eu-central-1")
    key = load_pem_public_key(key_loader(region, header["kid"]))
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        key.verify(der, f"{h64}.{p64}".encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise NotSignedIn("bad token signature") from e
    if claims.get("exp") and claims["exp"] < time.time():
        raise NotSignedIn("token expired")
    return claims


# ------------------------------------------------------------------ any OIDC provider

_JWKS_TTL = 3600.0
_jwks_cache: dict[str, tuple[float, dict]] = {}


def _http_json(url: str) -> dict:
    import httpx

    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def _jwks_url() -> str:
    if os.environ.get("ALC_OIDC_JWKS_URL"):
        return os.environ["ALC_OIDC_JWKS_URL"]
    issuer = os.environ.get("ALC_OIDC_ISSUER", "").rstrip("/")
    if not issuer:
        raise NotSignedIn("ALC_AUTH=oidc needs ALC_OIDC_ISSUER (or ALC_OIDC_JWKS_URL)")
    return _http_json(f"{issuer}/.well-known/openid-configuration")["jwks_uri"]


def _signing_key(kid: str | None, *, loader=_http_json) -> dict:
    """The provider's published key with this id; the key set is cached and re-read once for an unknown id."""
    url = _jwks_url()
    at, keys = _jwks_cache.get(url, (0.0, {}))
    if time.time() - at > _JWKS_TTL or kid not in keys:
        keys = {k.get("kid"): k for k in loader(url).get("keys", [])}
        _jwks_cache[url] = (time.time(), keys)
    if kid not in keys:
        if len(keys) == 1 and kid is None:
            return next(iter(keys.values()))
        raise NotSignedIn("token signed with an unknown key")
    return keys[kid]


def _public_key(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    if jwk.get("kty") == "RSA":
        return rsa.RSAPublicNumbers(int.from_bytes(_b64(jwk["e"]), "big"),
                                    int.from_bytes(_b64(jwk["n"]), "big")).public_key()
    if jwk.get("kty") == "EC":
        curve = {"P-256": ec.SECP256R1(), "P-384": ec.SECP384R1()}[jwk["crv"]]
        return ec.EllipticCurvePublicNumbers(int.from_bytes(_b64(jwk["x"]), "big"),
                                             int.from_bytes(_b64(jwk["y"]), "big"), curve).public_key()
    raise NotSignedIn(f"unsupported key type {jwk.get('kty')}")


_ALGS = {"RS256": ("RSA", "SHA256"), "RS384": ("RSA", "SHA384"), "RS512": ("RSA", "SHA512"),
         "ES256": ("EC", "SHA256"), "ES384": ("EC", "SHA384")}


def verify_oidc_jwt(token: str, *, key_loader=None, leeway: float = 60) -> dict:
    """Check an OIDC ID token (or JWT access token) against the provider's keys, issuer, audience and expiry."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    try:
        h64, p64, s64 = token.split(".")
        header, claims, sig = json.loads(_b64(h64)), json.loads(_b64(p64)), _b64(s64)
    except ValueError as e:
        raise NotSignedIn("missing or malformed bearer token (is the sign-in proxy passing the ID token?)") from e
    alg = header.get("alg")
    if alg not in _ALGS:
        raise NotSignedIn(f"unexpected token algorithm {alg!r}")
    kty, hash_name = _ALGS[alg]
    jwk = _signing_key(header.get("kid"), loader=key_loader or _http_json)
    if jwk.get("kty") != kty:
        raise NotSignedIn("token algorithm does not match its key")
    key, signed, h = _public_key(jwk), f"{h64}.{p64}".encode(), getattr(hashes, hash_name)()
    try:
        if kty == "RSA":
            key.verify(sig, signed, padding.PKCS1v15(), h)
        else:
            half = len(sig) // 2
            der = encode_dss_signature(int.from_bytes(sig[:half], "big"), int.from_bytes(sig[half:], "big"))
            key.verify(der, signed, ec.ECDSA(h))
    except InvalidSignature as e:
        raise NotSignedIn("bad token signature") from e
    issuer = os.environ.get("ALC_OIDC_ISSUER", "").rstrip("/")
    if issuer and str(claims.get("iss", "")).rstrip("/") != issuer:
        raise NotSignedIn("token is from another issuer (check ALC_OIDC_ISSUER)")
    audience = os.environ.get("ALC_OIDC_AUDIENCE")
    if not audience:
        raise NotSignedIn("ALC_AUTH=oidc needs ALC_OIDC_AUDIENCE (the client id the tokens are issued to)")
    aud = claims.get("aud")
    if audience not in (aud if isinstance(aud, list) else [aud]):
        raise NotSignedIn("token is for another application (check ALC_OIDC_AUDIENCE)")
    now = time.time()
    if not claims.get("exp") or claims["exp"] + leeway < now:
        raise NotSignedIn("token expired")
    if claims.get("nbf") and claims["nbf"] - leeway > now:
        raise NotSignedIn("token not valid yet")
    return claims


def principal_from_claims(svc, claims: dict) -> Principal:
    if hasattr(svc.access, "user_from_claims"):
        # The access model maps claims itself (group patterns configurable in access.yaml `identity:`).
        user = svc.access.user_from_claims(claims)
        return Principal(user=user, is_catalog_admin=user.is_admin or bool(getattr(user, "is_catalog_admin", False)),
                         email=claims.get("email"), name=claims.get("name") or claims.get("email") or user.id)
    groups_claim = os.environ.get("ALC_OIDC_GROUPS_CLAIM") or os.environ.get("ALC_GROUP_CLAIM", "groups")
    groups = claims.get(groups_claim) or []
    if isinstance(groups, str):
        groups = [g for g in groups.replace(",", " ").split() if g]
    user_id = claims.get(os.environ.get("ALC_OIDC_USER_CLAIM", "email")) or claims.get("sub")
    if not user_id:
        raise NotSignedIn("token has no user id claim")
    user = user_from_groups(str(user_id), groups)
    return Principal(user=user, is_catalog_admin=user.is_admin or "alc-catalog-admin" in groups,
                     email=claims.get("email"), name=claims.get("name") or claims.get("email"))


def user_from_groups(user_id: str, groups: list[str]) -> User:
    """Map identity-provider groups to an access User by the alc-* naming convention."""
    plants = frozenset(g.removeprefix("alc-plant-").upper() for g in groups if g.startswith("alc-plant-"))
    workspaces = frozenset(g.removeprefix("alc-ws-") for g in groups if g.startswith("alc-ws-"))
    return User(id=user_id, plants=plants, is_global="alc-global" in groups, is_admin="alc-admin" in groups,
                workspaces=workspaces)
