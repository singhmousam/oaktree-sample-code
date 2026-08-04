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
| Cosmos DB (Table API) | Stores file metadata |
| Function App (Elastic Premium, Linux, container) | Scans the share, writes metadata |
| Container Apps environment + Container App | Hosts the FastAPI UI |

## Prerequisites

- Azure CLI (`az`) installed and logged in (`az login`)
- Contributor (or Owner) access on the target subscription, since the
  script creates role assignments
- Bash (macOS/Linux, or WSL/Git Bash on Windows)
- No local Docker required — images are built remotely with `az acr build`

## Configure

Edit `config.env` — every value has a comment explaining it. At minimum you
may want to change `PREFIX` and `LOCATION`.
## Setup AZ CLI
Download and install AZ CLI from https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows?view=azure-cli-latest&pivots=msi

Run ```az login```
And choose default Sub ID, press Enter to continue
Move to coresponding directory, ```cd ./azure-demo-app``` and run the deploy.sh file

## Deploy

```bash
./deploy.sh
```

This is idempotent: a random suffix is generated once and saved to
`.deploy_state`, so re-running the script targets the same resources instead
of creating duplicates.

The script takes roughly 10–15 minutes, mostly waiting on the Cosmos DB
account and the Elastic Premium plan to provision.

At the end it prints the FastAPI app's public URL and a sample upload
command, e.g.:

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

## Cost note

The Function App's Elastic Premium plan (EP1) is billed hourly even when
idle — it's required for custom container support on Linux Function Apps.
For a short demo, destroy the stack when you're done to avoid ongoing
charges.

## Project layout

```
config.env             deployment parameters
deploy.sh               deploy / destroy script
function_app/           Azure Function (timer trigger, Python v2 model)
fastapi_app/            FastAPI UI (Jinja2 template)
```
