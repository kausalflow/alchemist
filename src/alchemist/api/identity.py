"""Who is calling: the signed-in user and what they may see.

Two modes, chosen by ALC_AUTH:

    dev       (default) the user id comes from the `X-Alchemist-User` header or the `alc_user` cookie and is looked
              up in access.yaml. For laptops and tests only: anyone can claim any user.
    alb-oidc  the corporate identity provider signs users in at the load balancer, which passes the claims in the
              signed `x-amzn-oidc-data` header. The signature, signer (the ALB's ARN) and expiry are checked, and
              group claims are mapped to plants and workspaces by the naming convention in the technical design:
              alc-plant-<code>, alc-global, alc-ws-<area>, alc-catalog-admin, alc-admin.

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
