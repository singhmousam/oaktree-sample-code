"""
Azure AD (Microsoft Entra ID) authentication backend.

This is the "azuread" AUTH_MODE backend (see auth.py, the facade that
picks between this and auth_jwt.py based on the AUTH_MODE env var).

Unlike auth_jwt.py, this app does NOT implement sign-in, token issuance, or
JWT validation itself. Azure Container Apps' built-in authentication
("Easy Auth") sits in front of the container as a sidecar: it redirects
unauthenticated browsers to Microsoft Entra ID to sign in, validates the
resulting token itself, and only then forwards the request to this
container — with the caller's identity attached in a request header,
`X-MS-CLIENT-PRINCIPAL` (base64-encoded JSON). This module's only job is to
read that header and map it to the same {"username", "role"} shape
auth_jwt.py produces, so main.py doesn't care which backend is active.

Roles come from Entra ID App Roles (Reader/Writer/Admin), assigned to
specific users in the tenant — see docs/azure-ad-setup.md and
scripts/setup_azure_ad.sh. A user with no app role assigned authenticates
successfully (Entra ID confirms who they are) but is denied here (nobody
gets default access — every permission is an explicit assignment).

Because Easy Auth already validates the token's signature, issuer, and
audience before this code ever runs, there's no signature-checking or JWKS
fetching here — trusting X-MS-CLIENT-PRINCIPAL is safe specifically because
Container Apps' platform strips/overwrites any client-supplied copy of that
header; it cannot be spoofed by an external caller. That trust boundary
only holds for traffic that actually went through Container Apps ingress,
which is the only way in for this app.
"""

import base64
import binascii
import json

from fastapi import HTTPException, Request, status

from roles import highest_role, make_require_min_role

PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"
PRINCIPAL_NAME_HEADER = "X-MS-CLIENT-PRINCIPAL-NAME"
ROLE_CLAIM_TYPES = {
    "roles",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
}


def _decode_principal(request: Request) -> dict | None:
    raw = request.headers.get(PRINCIPAL_HEADER)
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
        return json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None


def get_current_user(request: Request) -> dict:
    principal = _decode_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (no Easy Auth principal on the request)",
        )

    claims = principal.get("claims", [])
    role_claim_values = [c.get("val") for c in claims if c.get("typ") in ROLE_CLAIM_TYPES]
    role = highest_role(role_claim_values)

    username = (
        request.headers.get(PRINCIPAL_NAME_HEADER)
        or next((c.get("val") for c in claims if c.get("typ") == "preferred_username"), None)
        or principal.get("userDetails")
        or "unknown"
    )

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"'{username}' signed in successfully but has no Reader/Writer/Admin "
                "app role assigned. Ask an admin to assign one in Entra ID "
                "(Enterprise applications > this app > Users and groups)."
            ),
        )

    return {"username": username, "role": role}


def get_current_user_optional(request: Request) -> dict | None:
    try:
        return get_current_user(request)
    except HTTPException:
        return None


require_min_role = make_require_min_role(get_current_user)
