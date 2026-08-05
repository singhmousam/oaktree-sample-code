#!/usr/bin/env bash
###############################################################################
# upload_to_fileshare.sh — upload local files to the demo's Azure File Share.
#
# Usage:
#   ./scripts/upload_to_fileshare.sh                       # config.env + .deploy_state + ./data
#   ./scripts/upload_to_fileshare.sh -d ./my-files          # custom source directory
#   ./scripts/upload_to_fileshare.sh -a mystorage -s uploads -d ./data   # fully manual
#   ./scripts/upload_to_fileshare.sh -k                     # use account key instead of your login
#
# Options:
#   -a  Storage account name (default: derived from config.env + .deploy_state)
#   -s  File share name      (default: FILE_SHARE_NAME from config.env, usually "uploads")
#   -d  Local source directory to upload from (default: ./data)
#   -k  Authenticate with the storage account key instead of `az login`
#   -h  Show this help
#
# Auth notes:
#   By default this uses YOUR OWN `az login` identity (--auth-mode login), not
#   a managed identity (those belong to the deployed Function/FastAPI app only).
#   If you get a permission/authorization error, either:
#     a) grant yourself a data role on the storage account once:
#          az role assignment create \
#            --assignee "$(az ad signed-in-user show --query id -o tsv)" \
#            --role "Storage File Data Privileged Contributor" \
#            --scope "$(az storage account show -n <storage-account> --query id -o tsv)"
#        (role propagation can take a few minutes)
#     b) or re-run this script with -k to fall back to the storage account key.
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

STORAGE_NAME=""
SHARE_NAME=""
SOURCE_DIR="${PROJECT_DIR}/data"
USE_KEY=false

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while getopts "a:s:d:kh" opt; do
  case "$opt" in
    a) STORAGE_NAME="$OPTARG" ;;
    s) SHARE_NAME="$OPTARG" ;;
    d) SOURCE_DIR="$OPTARG" ;;
    k) USE_KEY=true ;;
    h) usage ;;
    *) usage ;;
  esac
done

# Fill in anything not passed explicitly from config.env / .deploy_state
if [[ -z "$STORAGE_NAME" || -z "$SHARE_NAME" ]]; then
  # shellcheck source=/dev/null
  [[ -f "${PROJECT_DIR}/config.env" ]] && source "${PROJECT_DIR}/config.env"
  # shellcheck source=/dev/null
  [[ -f "${PROJECT_DIR}/.deploy_state" ]] && source "${PROJECT_DIR}/.deploy_state"
  [[ -z "$STORAGE_NAME" ]] && STORAGE_NAME="$(echo "st${PREFIX:-}${SUFFIX:-}" | cut -c1-24)"
  [[ -z "$SHARE_NAME" ]] && SHARE_NAME="${FILE_SHARE_NAME:-uploads}"
fi

if [[ -z "$STORAGE_NAME" || "$STORAGE_NAME" == "st" ]]; then
  echo "Could not determine the storage account name. Pass it explicitly with -a <name>."
  exit 1
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source directory '$SOURCE_DIR' does not exist. Create it and add files, or pass -d <dir>."
  exit 1
fi

FILE_COUNT=$(find "$SOURCE_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')
if [[ "$FILE_COUNT" -eq 0 ]]; then
  echo "No files found directly inside '$SOURCE_DIR'. Add some files and re-run."
  exit 1
fi

echo "Uploading ${FILE_COUNT} file(s) from '${SOURCE_DIR}' to share '${SHARE_NAME}' on account '${STORAGE_NAME}'..."

if [[ "$USE_KEY" == true ]]; then
  ACCOUNT_KEY="$(az storage account keys list --account-name "$STORAGE_NAME" --query "[0].value" -o tsv)"
  az storage file upload-batch \
    --destination "$SHARE_NAME" \
    --source "$SOURCE_DIR" \
    --account-name "$STORAGE_NAME" \
    --account-key "$ACCOUNT_KEY"
else
  az storage file upload-batch \
    --destination "$SHARE_NAME" \
    --source "$SOURCE_DIR" \
    --account-name "$STORAGE_NAME" \
    --auth-mode login
fi

echo "Done. Files are now in the '${SHARE_NAME}' share — the Function will pick them up on its next timer run."
