"""
Minimal, self-contained JWT authentication & role-based authorization.

No external identity provider is used. User credentials (hashed) and the
JWT signing key both live as Key Vault secrets, read via the app's managed
identity — the exact same pattern already used for the storage account key
in clients.py. Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib only,
no bcrypt dependency); see scripts/generate_users_secret.py, which is run
once at deploy time to seed the hashed user list.

Roles form a simple hierarchy — a role grants everything at or below it:
    reader (1) : view uploaded file records
    writer (2) : reader + upload files / trigger a share scan
    admin  (3) : writer + delete a file metadata record

Two ways to obtain a token:
    POST /auth/login  -> browser flow, sets an HttpOnly cookie, redirects
    POST /auth/token   -> API flow, returns the JWT as JSON

Both are validated the same way: every protected route resolves the
current user via get_current_user(), which accepts either the cookie or an
`Authorization: Bearer <token>` header.

This is intentionally minimal, as a demo of the mechanics end-to-end (issue
a JWT, validate it, authorize by role) — see the README's Authentication
section for what a production version of this would need on top (account
lockout/rate limiting, refresh tokens, revocation, secret rotation, etc).
"""

import binascii
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status

from clients import get_secret_client

JWT_SECRET_NAME = os.environ.get("JWT_SECRET_NAME", "jwt-signing-key")
APP_USERS_SECRET_NAME = os.environ.get("APP_USERS_SECRET_NAME", "app-users")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

ROLE_RANK = {"reader": 1, "writer": 2, "admin": 3}

_jwt_secret_cache: str | None = None
_users_cache: list[dict] | None = None


def _jwt_secret() -> str:
    global _jwt_secret_cache
    if _jwt_secret_cache is None:
        _jwt_secret_cache = get_secret_client().get_secret(JWT_SECRET_NAME).value
    return _jwt_secret_cache


def _load_users() -> list[dict]:
    global _users_cache
    if _users_cache is None:
        raw = get_secret_client().get_secret(APP_USERS_SECRET_NAME).value
        _users_cache = json.loads(raw)
    return _users_cache


def _find_user(username: str) -> dict | None:
    for user in _load_users():
        if user["username"] == username:
            return user
    return None


def verify_password(password: str, password_hash: str) -> bool:
    """Must match the hashing scheme in scripts/generate_users_secret.py exactly."""
    try:
        scheme, iterations, salt_hex, hash_hex = password_hash.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(hash_hex)
    except (ValueError, binascii.Error):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    return hmac.compare_digest(candidate, expected)


def authenticate(username: str, password: str) -> dict | None:
    """Returns the user record on success, None on bad username/password."""
    user = _find_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def _extract_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
    return request.cookies.get("access_token")


def get_current_user(request: Request) -> dict:
    """Validates the JWT (from cookie or Authorization header). Raises 401 if missing/invalid/expired."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return {"username": payload["sub"], "role": payload["role"]}


def get_current_user_optional(request: Request) -> dict | None:
    """Same as get_current_user, but returns None instead of raising — for pages that redirect to /login."""
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def require_min_role(min_role: str):
    """Dependency factory: 403s unless the caller's role is >= min_role in the hierarchy."""
    required_rank = ROLE_RANK[min_role]

    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK.get(user["role"], 0) < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{min_role}' or higher required (you are '{user['role']}')",
            )
        return user

    return _dependency
