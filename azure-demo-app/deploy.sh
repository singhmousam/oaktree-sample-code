#!/usr/bin/env bash
###############################################################################
# deploy.sh — End-to-end deploy (or destroy) of the file-upload demo stack.
#
# Stack:
#   Storage Account + File Share  -> uploads land here
#   Azure Function (container, timer trigger) -> scans the share, writes
#                                                 metadata to Cosmos DB
#   Cosmos DB (Table API)         -> stores file metadata
#   FastAPI app (Container Apps)  -> reads Cosmos, shows an HTML table
#   ACR                           -> builds/hosts both container images
#   2x User-Assigned Managed Identity -> all service-to-service auth,
#                                         no connection strings / keys used
#
# Usage:
#   ./deploy.sh                 # deploy everything
#   ./deploy.sh --destroy       # tear everything down
#   ./deploy.sh --config other.env   # use a different config file
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.env"
STATE_FILE="${SCRIPT_DIR}/.deploy_state"
DESTROY=false
SUFFIX='dev'
###############################################################################
# Argument parsing
###############################################################################
while [[ $# -gt 0 ]]; do
  case "$1" in
    --destroy) DESTROY=true; shift ;;
    --config) CONFIG_FILE="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# shellcheck source=/dev/null
source "$CONFIG_FILE"
echo "Read the config file"
echo "Initializing the deployment process"
###############################################################################
# Derive resource names. A random suffix is generated once and persisted in
# .deploy_state so that re-running deploy.sh (or destroy) is idempotent and
# always targets the same set of resources.
###############################################################################
# if [[ -f "$STATE_FILE" ]]; then
#   # shellcheck source=/dev/null
#   source "$STATE_FILE"
# else
#   # SUFFIX="$(tr -dc 'a-z0-9' </dev/urandom | head -c 3)"
#   SUFFIX="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 3)"
#   echo "SUFFIX=${SUFFIX}" > "$STATE_FILE"
# fi

# SUFFIX="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 3)"
echo "SUFFIX=${SUFFIX}" > "$STATE_FILE"
echo "Setting resource names"
RG_NAME="rg-${PREFIX}-${SUFFIX}"
ACR_NAME="acr${PREFIX}${SUFFIX}"                                   # alnum only
STORAGE_NAME="$(echo "st${PREFIX}${SUFFIX}" | cut -c1-24)"         # <=24 chars, alnum
COSMOS_NAME="cosmos-${PREFIX}-${SUFFIX}"
ID_FUNC_NAME="id-func-${PREFIX}-${SUFFIX}"
ID_API_NAME="id-api-${PREFIX}-${SUFFIX}"
PLAN_NAME="plan-func-${PREFIX}-${SUFFIX}"
FUNCTION_APP_NAME="func-${PREFIX}-${SUFFIX}"
CAE_NAME="cae-${PREFIX}-${SUFFIX}"
CONTAINERAPP_NAME="ca-${PREFIX}-${SUFFIX}"

FUNC_IMAGE="function-app"
API_IMAGE="fastapi-app"
FUNC_IMAGE_TAG="latest"
API_IMAGE_TAG="latest"

log()  { echo -e "\n\033[1;34m==>\033[0m $*"; }
ok()   { echo -e "\033[1;32m✓\033[0m $*"; }

echo "Checking Prerequisities"
###############################################################################
# Helpers
###############################################################################
check_prereqs() {
  command -v az >/dev/null 2>&1 || { echo "Azure CLI ('az') is required."; exit 1; }
  az account show >/dev/null 2>&1 || { echo "Please run 'az login' first."; exit 1; }
  if [[ -n "${SUBSCRIPTION_ID}" ]]; then
    az account set --subscription "${SUBSCRIPTION_ID}"
  fi
  ok "Azure CLI ready, subscription: $(az account show --query name -o tsv)"
}

###############################################################################
# Destroy
###############################################################################
destroy_all() {
  log "Deleting resource group ${RG_NAME} (this deletes every resource in it)"
  if az group show -n "$RG_NAME" >/dev/null 2>&1; then
    az group delete -n "$RG_NAME" --yes --no-wait
    ok "Delete requested (running in background). Check with: az group show -n ${RG_NAME}"
  else
    echo "Resource group ${RG_NAME} does not exist — nothing to delete."
  fi
  rm -f "$STATE_FILE"
  ok "Removed local state file. Destroy complete."
  exit 0
}

echo "Creating resources"
###############################################################################
# 1. Resource group
###############################################################################
create_resource_group() {
  log "Creating resource group ${RG_NAME} in ${LOCATION}"
  az group create -n "$RG_NAME" -l "$LOCATION" -o none
  ok "Resource group ready"
}

###############################################################################
# 2. ACR
###############################################################################
create_acr() {
  log "Creating Azure Container Registry ${ACR_NAME}"
  az acr create -g "$RG_NAME" -n "$ACR_NAME" --sku "$ACR_SKU" -o none
  ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)"
  ok "ACR ready: ${ACR_LOGIN_SERVER}"
}

###############################################################################
# 3. Managed identities
###############################################################################
create_identities() {
  log "Creating user-assigned managed identities"
  az identity create -g "$RG_NAME" -n "$ID_FUNC_NAME" -o none
  az identity create -g "$RG_NAME" -n "$ID_API_NAME" -o none

  ID_FUNC_ID="$(az identity show -g "$RG_NAME" -n "$ID_FUNC_NAME" --query id -o tsv)"
  ID_FUNC_CLIENT_ID="$(az identity show -g "$RG_NAME" -n "$ID_FUNC_NAME" --query clientId -o tsv)"
  ID_FUNC_PRINCIPAL_ID="$(az identity show -g "$RG_NAME" -n "$ID_FUNC_NAME" --query principalId -o tsv)"

  ID_API_ID="$(az identity show -g "$RG_NAME" -n "$ID_API_NAME" --query id -o tsv)"
  ID_API_CLIENT_ID="$(az identity show -g "$RG_NAME" -n "$ID_API_NAME" --query clientId -o tsv)"
  ID_API_PRINCIPAL_ID="$(az identity show -g "$RG_NAME" -n "$ID_API_NAME" --query principalId -o tsv)"

  ok "Identities ready (func: ${ID_FUNC_CLIENT_ID}, api: ${ID_API_CLIENT_ID})"
}

###############################################################################
# 4. Storage account + file share
###############################################################################
create_storage() {
  log "Creating storage account ${STORAGE_NAME} and file share ${FILE_SHARE_NAME}"
  az storage account create \
    -g "$RG_NAME" -n "$STORAGE_NAME" -l "$LOCATION" \
    --sku "$STORAGE_SKU" --kind StorageV2 -o none

  # Share creation needs a data-plane credential; the deploying user's own
  # az login context is used here just for this one-time setup step.
  az storage share-rm create \
    -g "$RG_NAME" --storage-account "$STORAGE_NAME" \
    --name "$FILE_SHARE_NAME" --quota 10 -o none

  STORAGE_ID="$(az storage account show -g "$RG_NAME" -n "$STORAGE_NAME" --query id -o tsv)"
  ok "Storage account and file share ready"
}

###############################################################################
# 5. Cosmos DB (Table API)
###############################################################################
create_cosmos() {
  log "Creating Cosmos DB account ${COSMOS_NAME} (Table API)"
  az cosmosdb create \
    -g "$RG_NAME" -n "$COSMOS_NAME" \
    --capabilities EnableTable \
    --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=False \
    -o none

  az cosmosdb table create \
    -g "$RG_NAME" -a "$COSMOS_NAME" \
    -n "$COSMOS_TABLE_NAME" --throughput "$COSMOS_THROUGHPUT" -o none

  COSMOS_ID="$(az cosmosdb show -g "$RG_NAME" -n "$COSMOS_NAME" --query id -o tsv)"
  COSMOS_TABLE_ENDPOINT="https://${COSMOS_NAME}.table.cosmos.azure.com:443/"
  ok "Cosmos DB ready: ${COSMOS_TABLE_ENDPOINT}"
}

###############################################################################
# 6. Role assignments (all access is managed-identity based — no keys)
###############################################################################
assign_roles() {
  log "Assigning RBAC roles to managed identities"

  # --- ACR pull, for both identities ---
  az role assignment create --assignee-object-id "$ID_FUNC_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "AcrPull" --scope "$(az acr show -n "$ACR_NAME" --query id -o tsv)" -o none
  az role assignment create --assignee-object-id "$ID_API_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "AcrPull" --scope "$(az acr show -n "$ACR_NAME" --query id -o tsv)" -o none

  # --- Storage: Function identity needs the runtime's own storage roles
  #     (for identity-based AzureWebJobsStorage) plus file share read access ---
  for role in "Storage Blob Data Owner" "Storage Queue Data Contributor" "Storage Table Data Contributor" "Storage File Data Privileged Contributor"; do
    az role assignment create --assignee-object-id "$ID_FUNC_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role "$role" --scope "$STORAGE_ID" -o none
  done

  # --- Cosmos DB data-plane RBAC (works for the Table API too — it shares
  #     the same account-level data-plane role system) ---
  CONTRIBUTOR_ROLE_ID="00000000-0000-0000-0000-000000000002"   # built-in: Data Contributor
  READER_ROLE_ID="00000000-0000-0000-0000-000000000001"        # built-in: Data Reader

  az cosmosdb sql role assignment create \
    -g "$RG_NAME" -a "$COSMOS_NAME" \
    --role-definition-id "$CONTRIBUTOR_ROLE_ID" \
    --principal-id "$ID_FUNC_PRINCIPAL_ID" \
    --scope "$COSMOS_ID" -o none

  az cosmosdb sql role assignment create \
    -g "$RG_NAME" -a "$COSMOS_NAME" \
    --role-definition-id "$READER_ROLE_ID" \
    --principal-id "$ID_API_PRINCIPAL_ID" \
    --scope "$COSMOS_ID" -o none

  ok "Role assignments complete"
}

###############################################################################
# 7. Build & push both container images (built inside ACR — no local Docker needed)
###############################################################################
build_images() {
  log "Building and pushing the Function App image"
  az acr build -r "$ACR_NAME" -t "${FUNC_IMAGE}:${FUNC_IMAGE_TAG}" "${SCRIPT_DIR}/function_app" -o none

  log "Building and pushing the FastAPI app image"
  az acr build -r "$ACR_NAME" -t "${API_IMAGE}:${API_IMAGE_TAG}" "${SCRIPT_DIR}/fastapi_app" -o none

  ok "Both images built and pushed to ${ACR_LOGIN_SERVER}"
}

###############################################################################
# 8. Deploy the Function App (Elastic Premium Linux plan, custom container)
###############################################################################
deploy_function() {
  log "Creating Function App hosting plan (${FUNCTION_PLAN_SKU}, Linux)"
  az functionapp plan create \
    -g "$RG_NAME" -n "$PLAN_NAME" --sku "$FUNCTION_PLAN_SKU" --is-linux -o none

  log "Creating Function App ${FUNCTION_APP_NAME}"
  az functionapp create \
    -g "$RG_NAME" -n "$FUNCTION_APP_NAME" \
    --plan "$PLAN_NAME" \
    --functions-version 4 \
    --image "${ACR_LOGIN_SERVER}/${FUNC_IMAGE}:${FUNC_IMAGE_TAG}" \
    --assign-identity "$ID_FUNC_ID" \
    -o none

  log "Configuring image pull from ACR via the managed identity (no admin creds)"
  az resource update \
    --ids "$(az functionapp show -g "$RG_NAME" -n "$FUNCTION_APP_NAME" --query id -o tsv)" \
    --set properties.siteConfig.acrUseManagedIdentityCreds=true \
    --set properties.siteConfig.acrUserManagedIdentityID="$ID_FUNC_CLIENT_ID" \
    -o none

  log "Configuring identity-based settings (no connection strings / keys)"
  az functionapp config appsettings set -g "$RG_NAME" -n "$FUNCTION_APP_NAME" --settings \
    "AzureWebJobsStorage__accountName=${STORAGE_NAME}" \
    "AzureWebJobsStorage__credential=managedidentity" \
    "AzureWebJobsStorage__clientId=${ID_FUNC_CLIENT_ID}" \
    "AZURE_CLIENT_ID=${ID_FUNC_CLIENT_ID}" \
    "STORAGE_ACCOUNT_NAME=${STORAGE_NAME}" \
    "FILE_SHARE_NAME=${FILE_SHARE_NAME}" \
    "COSMOS_TABLE_ENDPOINT=${COSMOS_TABLE_ENDPOINT}" \
    "COSMOS_TABLE_NAME=${COSMOS_TABLE_NAME}" \
    "TIMER_SCHEDULE=${TIMER_SCHEDULE}" \
    "FUNCTIONS_WORKER_RUNTIME=python" \
    -o none

  ok "Function App deployed: ${FUNCTION_APP_NAME}"
}

###############################################################################
# 9. Deploy the FastAPI app (Azure Container Apps)
###############################################################################
deploy_fastapi() {
  log "Ensuring Container Apps extension/providers are ready"
  az extension add --name containerapp --upgrade -o none 2>/dev/null || true
  az provider register --namespace Microsoft.App -o none
  az provider register --namespace Microsoft.OperationalInsights -o none

  log "Creating Container Apps environment ${CAE_NAME}"
  az containerapp env create -g "$RG_NAME" -n "$CAE_NAME" -l "$LOCATION" -o none

  log "Creating Container App ${CONTAINERAPP_NAME}"
  az containerapp create \
    -g "$RG_NAME" -n "$CONTAINERAPP_NAME" \
    --environment "$CAE_NAME" \
    --image "${ACR_LOGIN_SERVER}/${API_IMAGE}:${API_IMAGE_TAG}" \
    --target-port "$FASTAPI_PORT" \
    --ingress external \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-identity "$ID_API_ID" \
    --user-assigned "$ID_API_ID" \
    --env-vars \
      "AZURE_CLIENT_ID=${ID_API_CLIENT_ID}" \
      "COSMOS_TABLE_ENDPOINT=${COSMOS_TABLE_ENDPOINT}" \
      "COSMOS_TABLE_NAME=${COSMOS_TABLE_NAME}" \
    -o none

  APP_URL="https://$(az containerapp show -g "$RG_NAME" -n "$CONTAINERAPP_NAME" --query properties.configuration.ingress.fqdn -o tsv)"
  ok "FastAPI app deployed: ${APP_URL}"
}

###############################################################################
# 10. Summary
###############################################################################
print_summary() {
  log "Deployment complete"
  cat <<EOF

  Resource group     : ${RG_NAME}
  Storage account     : ${STORAGE_NAME}  (file share: ${FILE_SHARE_NAME})
  Cosmos DB account   : ${COSMOS_NAME}   (table: ${COSMOS_TABLE_NAME})
  ACR                 : ${ACR_LOGIN_SERVER}
  Function App        : ${FUNCTION_APP_NAME}  (polls the file share every ${TIMER_SCHEDULE})
  FastAPI app         : ${APP_URL}

  Try it:
    1. Upload a file to the '${FILE_SHARE_NAME}' file share (Portal, Storage Explorer, or:
       az storage file upload --account-name ${STORAGE_NAME} --share-name ${FILE_SHARE_NAME} \\
         --source ./somefile.txt --auth-mode login)
    2. Wait for the next Function timer run (up to ~2 min).
    3. Open ${APP_URL} to see the file listed.

  To remove everything:
    ./deploy.sh --destroy
EOF
}

###############################################################################
# Main
###############################################################################
check_prereqs

if [[ "$DESTROY" == true ]]; then
  destroy_all
fi

create_resource_group
create_acr
create_identities
create_storage
create_cosmos
assign_roles
build_images
# deploy_function
deploy_fastapi
print_summary
