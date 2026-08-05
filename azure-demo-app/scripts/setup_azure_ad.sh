#!/usr/bin/env bash
###############################################################################
# scripts/setup_azure_ad.sh — prerequisite for AUTH_MODE=azuread.
#
# Registers a Microsoft Entra ID (Azure AD) application for this demo,
# defines three App Roles (Reader/Writer/Admin), creates a client secret,
# and — if you've filled in AAD_USER_ROLE_ASSIGNMENTS in config.env —
# assigns those users to their roles.
#
# WHY THIS IS A SEPARATE SCRIPT FROM deploy.sh: registering an application
# in Entra ID needs directory-level permission (e.g. the "Application
# Administrator" role, or your tenant simply allowing members to register
# apps) — a different permission from Contributor on an Azure subscription
# or resource group, which is all deploy.sh otherwise needs. In many
# organizations these are two different teams/approvals. deploy.sh will run
# this automatically the first time AUTH_MODE=azuread is deployed (it knows
# the Container App's URL by then, needed for the redirect URI), but you can
# also run it yourself ahead of time — see docs/azure-ad-setup.md.
#
# Usage:
#   ./scripts/setup_azure_ad.sh --redirect-uri https://<app-fqdn>/.auth/login/aad/callback
#   ./scripts/setup_azure_ad.sh --redirect-uri <url> --display-name my-app --force
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${PROJECT_DIR}/.azuread_state"
CONFIG_FILE="${PROJECT_DIR}/config.env"

DISPLAY_NAME="azure-demo-app-auth"
REDIRECT_URI=""
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --redirect-uri) REDIRECT_URI="$2"; shift 2 ;;
    --display-name) DISPLAY_NAME="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$REDIRECT_URI" ]]; then
  echo "Usage: $0 --redirect-uri https://<app-fqdn>/.auth/login/aad/callback [--display-name NAME] [--force]"
  exit 1
fi

if [[ -f "$STATE_FILE" && "$FORCE" != true ]]; then
  echo "Found existing ${STATE_FILE} — Azure AD app already set up (use --force to redo it)."
  # shellcheck source=/dev/null
  source "$STATE_FILE"
  echo "  App (client) ID : ${AAD_APP_ID}"
  echo "  Tenant ID       : ${AAD_TENANT_ID}"
  exit 0
fi

command -v az >/dev/null 2>&1 || { echo "Azure CLI ('az') is required."; exit 1; }
az account show >/dev/null 2>&1 || { echo "Please run 'az login' first."; exit 1; }

TENANT_ID="$(az account show --query tenantId -o tsv)"

READER_ROLE_GUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
WRITER_ROLE_GUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
ADMIN_ROLE_GUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"

TMP_ROLES_FILE="$(mktemp /tmp/aad_app_roles.XXXXXX.json)"
trap 'rm -f "$TMP_ROLES_FILE"' EXIT

cat > "$TMP_ROLES_FILE" <<JSON
[
  {"allowedMemberTypes": ["User"], "displayName": "Reader", "id": "${READER_ROLE_GUID}", "isEnabled": true, "description": "Can view uploaded file records", "value": "Reader"},
  {"allowedMemberTypes": ["User"], "displayName": "Writer", "id": "${WRITER_ROLE_GUID}", "isEnabled": true, "description": "Can view, upload, and scan the file share", "value": "Writer"},
  {"allowedMemberTypes": ["User"], "displayName": "Admin",  "id": "${ADMIN_ROLE_GUID}",  "isEnabled": true, "description": "Can view, upload, scan, and delete file records", "value": "Admin"}
]
JSON

echo "==> Registering Entra ID application '${DISPLAY_NAME}'"
APP_ID="$(az ad app create \
  --display-name "$DISPLAY_NAME" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "$REDIRECT_URI" \
  --app-roles "@${TMP_ROLES_FILE}" \
  --query appId -o tsv)"

echo "==> Creating the app's service principal (enterprise application)"
az ad sp create --id "$APP_ID" -o none 2>/dev/null || true   # ok if it already exists
SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

echo "==> Creating a client secret (valid 1 year — rotate before it expires)"
CLIENT_SECRET="$(az ad app credential reset --id "$APP_ID" --years 1 --append --query password -o tsv)"

cat > "$STATE_FILE" <<EOF
AAD_APP_ID=${APP_ID}
AAD_SP_ID=${SP_ID}
AAD_TENANT_ID=${TENANT_ID}
AAD_CLIENT_SECRET=${CLIENT_SECRET}
AAD_ROLE_ID_READER=${READER_ROLE_GUID}
AAD_ROLE_ID_WRITER=${WRITER_ROLE_GUID}
AAD_ROLE_ID_ADMIN=${ADMIN_ROLE_GUID}
EOF
chmod 600 "$STATE_FILE"
echo "  Wrote ${STATE_FILE} (contains a client secret — this is already in .gitignore, don't commit it)"

# --- Optional: assign users to roles, from config.env's AAD_USER_ROLE_ASSIGNMENTS ---
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
fi

ASSIGNED_ANY=false
if [[ -n "${AAD_USER_ROLE_ASSIGNMENTS:-}" ]]; then
  echo "==> Assigning users to app roles"
  while IFS= read -r line; do
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" || "$line" == \#* ]] && continue
    UPN="${line%%:*}"
    ROLE="${line##*:}"
    case "$ROLE" in
      reader) ROLE_ID="$READER_ROLE_GUID" ;;
      writer) ROLE_ID="$WRITER_ROLE_GUID" ;;
      admin)  ROLE_ID="$ADMIN_ROLE_GUID" ;;
      *) echo "  Skipping '${line}': unknown role '${ROLE}' (expected reader/writer/admin)"; continue ;;
    esac
    USER_OBJECT_ID="$(az ad user show --id "$UPN" --query id -o tsv 2>/dev/null || true)"
    if [[ -z "$USER_OBJECT_ID" ]]; then
      echo "  Skipping '${UPN}': user not found in this tenant"
      continue
    fi
    if az rest --method POST \
      --uri "https://graph.microsoft.com/v1.0/users/${USER_OBJECT_ID}/appRoleAssignments" \
      --body "{\"principalId\":\"${USER_OBJECT_ID}\",\"resourceId\":\"${SP_ID}\",\"appRoleId\":\"${ROLE_ID}\"}" \
      -o none 2>/dev/null; then
      echo "  Assigned ${UPN} -> ${ROLE}"
      ASSIGNED_ANY=true
    else
      echo "  Could not assign ${UPN} -> ${ROLE} (already assigned? check the Portal if unsure)"
    fi
  done <<< "$AAD_USER_ROLE_ASSIGNMENTS"
fi

if [[ "$ASSIGNED_ANY" != true ]]; then
  echo ""
  echo "No users assigned yet. Either:"
  echo "  a) Fill in AAD_USER_ROLE_ASSIGNMENTS in config.env and re-run with --force, or"
  echo "  b) Assign them yourself in the Portal: Entra ID > Enterprise applications >"
  echo "     '${DISPLAY_NAME}' > Users and groups > Add user/group."
  echo "Nobody can sign in successfully until at least one user has a role."
fi

echo ""
echo "Azure AD app ready:"
echo "  App (client) ID : ${APP_ID}"
echo "  Tenant ID       : ${TENANT_ID}"
echo "  Redirect URI    : ${REDIRECT_URI}"
