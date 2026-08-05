"""
Auth facade — picks the active authentication backend based on the
AUTH_MODE environment variable, so main.py can just `import auth` and call
auth.get_current_user() etc. without caring which mechanism is behind it.

    AUTH_MODE=jwt      (default) -> auth_jwt.py:     this app's own login,
                                     JWT issuance and validation, Key Vault
                                     -stored user list. No external IdP.
    AUTH_MODE=azuread             -> auth_azuread.py: Microsoft Entra ID via
                                     Azure Container Apps' built-in
                                     authentication ("Easy Auth"). See
                                     docs/azure-ad-setup.md.

Both backends expose the same shape: get_current_user, get_current_user_optional,
require_min_role, ROLE_RANK — see roles.py for the shared role hierarchy.
"""

import os

AUTH_MODE = os.environ.get("AUTH_MODE", "jwt").lower()

if AUTH_MODE == "azuread":
    from auth_azuread import get_current_user, get_current_user_optional, require_min_role
else:
    from auth_jwt import (  # noqa: F401
        ACCESS_TOKEN_EXPIRE_MINUTES,
        authenticate,
        create_access_token,
        get_current_user,
        get_current_user_optional,
        require_min_role,
    )

from roles import ROLE_RANK  # noqa: F401,E402
