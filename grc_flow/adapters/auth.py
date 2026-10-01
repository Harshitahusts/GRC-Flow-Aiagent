"""Clerk authentication for the API.

The website (GRC-Ai) signs users in with Clerk and sends the Clerk session token
as `Authorization: Bearer <jwt>`. We verify it locally against Clerk's JWKS
(RS256): signature, expiry, not-before, issuer, and the `azp` (authorized party)
claim, which must be one of our own origins so tokens minted for another site
are refused. The Clerk organization id (`org_id`) scopes every query.

Service-to-service calls (n8n) don't use Clerk; they are HMAC-signed (see n8n.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import jwt
from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    org_role: str


@cache
def _jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def verify_clerk_token(
    token: str,
    issuer: str,
    authorized_parties: tuple[str, ...],
    jwks: jwt.PyJWKClient | None = None,
) -> Principal:
    if not issuer:
        raise HTTPException(503, "Authentication is not configured (CLERK_ISSUER).")
    try:
        key = (jwks or _jwks_client(issuer)).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid session token: {exc}") from None
    azp = claims.get("azp")
    if authorized_parties and azp not in authorized_parties:
        raise HTTPException(401, "Session token was issued for another site.")
    # Clerk v2 session tokens nest the active organization under "o"; v1 used org_id.
    org = claims.get("o") or {}
    org_id = org.get("id") or claims.get("org_id") or ""
    if not org_id:
        raise HTTPException(403, "Select an organization in the app first.")
    return Principal(
        user_id=claims["sub"], org_id=org_id, org_role=org.get("rol") or claims.get("org_role", "")
    )


def require_user(request: Request, authorization: str = Header(default="")) -> Principal:
    """FastAPI dependency: the signed-in Clerk user, or 401."""
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        if settings.production:
            raise HTTPException(503, "Dev auth is disabled in production.")
        user = request.headers.get("x-dev-user", "dev-user")
        org = request.headers.get("x-dev-org", "org_dev")
        return Principal(user_id=user, org_id=org, org_role="org:admin")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in required.")
    return verify_clerk_token(
        token,
        settings.clerk_issuer,
        settings.clerk_authorized_parties,
        getattr(request.app.state, "jwks", None),
    )


def require_admin(principal: Principal) -> Principal:
    if principal.org_role not in ("org:admin", "admin"):
        raise HTTPException(403, "Admins only.")
    return principal
