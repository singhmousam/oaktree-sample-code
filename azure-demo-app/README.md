# Azure File-Upload Demo Stack

A small end-to-end demo: drop a file into an Azure Files share, a Function
picks up its metadata and writes it to Cosmos DB, and a FastAPI app shows it
in a browser. Every service talks to the others using **managed identity** —
there are no connection strings, SAS tokens, or account keys anywhere in the
deployed configuration.

## Architecture

```
 Upload a file
      │
      ▼
┌─────────────────┐        polls every 2 min        ┌───────────────────────┐
│ Storage Account  │ ───────────────────────────────▶│  Azure Function        │
│  + File Share    │   (managed identity, OAuth)      │  (Timer trigger,       │
└─────────────────┘                                   │   container, EP1 plan) │
                                                       └───────────┬───────────┘
                                                                   │ writes metadata
                                                                   │ (managed identity)
                                                                   ▼
                                                       ┌───────────────────────┐
                                                       │  Cosmos DB             │
                                                       │  (Table API)           │
                                                       └───────────┬───────────┘
                                                                   │ reads metadata
                                                                   │ (managed identity)
                                                                   ▼
                                                       ┌───────────────────────┐
                                                       │  FastAPI app           │
                                                       │  (Container Apps)      │
                                                       └───────────────────────┘

              Both container images are built and hosted in an ACR,
              pulled by the Function App and Container App using their
              own user-assigned managed identities.
```

### Why a timer trigger instead of an "upload trigger"?

Azure Files does not support Event Grid or any native "file uploaded" event
(that capability exists only for Blob Storage). So instead of pretending an
event trigger exists, the Function uses a **timer trigger** that scans the
share on a schedule (every 2 minutes by default, configurable) and records
any file that's new or has changed since the last scan. This is the
correct, supported pattern for reacting to Azure Files uploads. If you'd
prefer true event-driven triggering, switch the storage layer to Blob
Storage + a Blob trigger or Event Grid subscription — happy to adapt the
code to that if you want it instead.

## What gets created

| Resource | Purpose |
|---|---|
| Resource Group | Container for everything |
| Azure Container Registry (ACR) | Builds & hosts both app images |
| 2x User-Assigned Managed Identity | One for the Function, one for the FastAPI app |
| Storage Account + File Share | Where files are uploaded |
| Key Vault | Holds the storage account key the FastAPI app uses for its own share access |
| Cosmos DB (Table API) | Stores file metadata |
| Function App (Elastic Premium, Linux, container) | Scans the share, writes metadata |
| Container Apps environment + Container App | Hosts the FastAPI UI |

## FastAPI app features

- **View recorded files** — the main table, sourced from Cosmos DB (as before).
- **Scan file share now** — a button that lists everything currently in the
  file share and upserts metadata for each file into Cosmos DB immediately,
  instead of waiting for the Function's next timer run.
- **Upload from the browser** — a form that uploads a file straight to the
  file share and records its metadata right away.

Every record shows a `Source` column (`azure-function`, `fastapi-scan`, or
`fastapi-upload`) so you can see which path produced it.

**Auth model for these two features specifically:** the FastAPI app talks to
Cosmos DB using its managed identity directly (OAuth), same as before. For
the file share, it instead reads the storage account key out of **Key
Vault** and uses that key — access to Key Vault itself is still via managed
identity (`Key Vault Secrets User` role, read-only), so the only secret
value anywhere in the whole stack is that one key, and only the FastAPI
identity can read it. The Function App is unaffected — it still talks to the
file share with pure OAuth (no key), as before.

## Prerequisites

- Azure CLI (`az`) installed and logged in (`az login`)
- Contributor (or Owner) access on the target subscription, since the
  script creates role assignments
- Bash (macOS/Linux, or WSL/Git Bash on Windows)
- No local Docker required — images are built remotely with `az acr build`

## Configure

Edit `config.env` — every value has a comment explaining it. At minimum you
may want to change `PREFIX` and `LOCATION`.

## Deploy

```bash
./deploy.sh
```

This is idempotent: a random suffix is generated once and saved to
`.deploy_state`, so re-running the script targets the same resources instead
of creating duplicates.

The script takes roughly 10–15 minutes, mostly waiting on the Cosmos DB
account and the Elastic Premium plan to provision.

At the end it prints the FastAPI app's public URL. From there you can just
open the app and use the "Upload to file share" form or the "Scan file
share now" button directly in the browser — no CLI needed.

If you'd rather upload via CLI and let the Function's timer pick it up:

```bash
az storage file upload \
  --account-name <storage-account> \
  --share-name uploads \
  --source ./somefile.txt \
  --auth-mode login
```

Wait for the next timer run (up to ~2 minutes), then refresh the app URL.

## Destroy

```bash
./deploy.sh --destroy
```

Deletes the whole resource group (background operation) and clears the
local state file.

## Helper scripts

Two standalone scripts under `scripts/` are useful for testing and as a
fallback if the Azure Function gives you trouble:

### `scripts/upload_to_fileshare.sh`
Uploads whatever's in a local directory (default `./data`) into the Azure
File Share, so you don't have to use the Portal or Storage Explorer:

```bash
./scripts/upload_to_fileshare.sh              # uploads ./data using your az login
./scripts/upload_to_fileshare.sh -d ./myfiles # a different source directory
./scripts/upload_to_fileshare.sh -k           # fall back to the account key if
                                               # your own login lacks a data role
```

### `scripts/simulate_local_ingestion.py`
If the Function App isn't deploying or running correctly, this script does
its job by hand: it scans a local directory (default `./data`), computes the
same metadata (name, size, type, modified time), and writes it straight to
Cosmos DB — no Function required. Records it writes are tagged
`Source=local-simulator` so you can tell them apart from real Function runs
(tagged `Source=azure-function`) in the FastAPI UI.

```bash
pip install -r scripts/requirements.txt

python3 scripts/simulate_local_ingestion.py \
  --cosmos-endpoint https://<cosmos-account>.table.cosmos.azure.com:443/ \
  --cosmos-table filemetadata

# preview without writing anything:
python3 scripts/simulate_local_ingestion.py --dry-run
```

Both scripts use your own `az login` identity, not the deployed managed
identities. `deploy.sh` only grants Cosmos/Storage data roles to the
Function and FastAPI managed identities — if you get a permission error
running these scripts yourself, grant your own account the same roles once:

```bash
# Cosmos DB (lets you write metadata manually)
az cosmosdb sql role assignment create \
  --resource-group <rg> --account-name <cosmos-account-name> \
  --role-definition-id 00000000-0000-0000-0000-000000000002 \
  --principal-id "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az cosmosdb show -g <rg> -n <cosmos-account-name> --query id -o tsv)"

# Storage File Share (lets you upload via --auth-mode login instead of -k)
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage File Data Privileged Contributor" \
  --scope "$(az storage account show -n <storage-account-name> --query id -o tsv)"
```

## Cost note

The Function App's Elastic Premium plan (EP1) is billed hourly even when
idle — it's required for custom container support on Linux Function Apps.
For a short demo, destroy the stack when you're done to avoid ongoing
charges.

## Project layout

```
config.env                          deployment parameters
deploy.sh                           deploy / destroy script
function_app/                       Azure Function (timer trigger, Python v2 model)
fastapi_app/                        FastAPI UI (Jinja2 template) + scan/upload endpoints
fastapi_app/clients.py              shared Key Vault / file share / Cosmos client helpers
scripts/upload_to_fileshare.sh      upload local files to the file share
scripts/simulate_local_ingestion.py fallback: writes metadata to Cosmos DB without the Function
data/                               local scratch folder used by both scripts above
```
