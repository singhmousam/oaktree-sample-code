"""
Shared role hierarchy and authorization logic, used by both auth backends
(auth_jwt.py and auth_azuread.py) so the reader/writer/admin rules are
defined exactly once regardless of which sign-in mechanism is active.
"""

from fastapi import Depends, HTTPException, status

ROLE_RANK = {"reader": 1, "writer": 2, "admin": 3}


def highest_role(candidate_roles: list[str]) -> str | None:
    """Given a list of role names/values from a token or claims set, return the
    highest-ranked one recognized (case-insensitive). None if none match."""
    best = None
    best_rank = 0
    for role in candidate_roles:
        key = (role or "").strip().lower()
        rank = ROLE_RANK.get(key, 0)
        if rank > best_rank:
            best = key
            best_rank = rank
    return best


def make_require_min_role(get_current_user):
    """Dependency factory: 403s unless the caller's role outranks min_role."""

    def require_min_role(min_role: str):
        required_rank = ROLE_RANK[min_role]

        def _dependency(user: dict = Depends(get_current_user)) -> dict:
            if ROLE_RANK.get(user["role"], 0) < required_rank:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Role '{min_role}' or higher required (you are '{user['role']}')",
                )
            return user

        return _dependency

    return require_min_role
