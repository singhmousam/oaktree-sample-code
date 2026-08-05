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
| Key Vault | Holds the storage account key (file share access), JWT signing key, and hashed demo user list |
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
- **Delete a record** — admins only.

Every record shows a `Source` column (`azure-function`, `fastapi-scan`, or
`fastapi-upload`) so you can see which path produced it.

**Auth model for the file share vs. Cosmos:** the FastAPI app talks to
Cosmos DB using its managed identity directly (OAuth). For the file share,
it instead reads the storage account key out of **Key Vault** and uses that
key — access to Key Vault itself is still via managed identity (`Key Vault
Secrets User` role, read-only), so no key is ever hardcoded or passed
around. The Function App is unaffected — it still talks to the file share
with pure OAuth (no key), as before.

## Authentication & Authorization

The app supports **two interchangeable auth mechanisms**, switched with one
setting — `AUTH_MODE` in `config.env` — with no code changes required
either way. Both enforce the same reader/writer/admin role hierarchy:

| Role | Can do |
|---|---|
| `reader` | View the recorded files table |
| `writer` | Everything `reader` can, plus upload files and trigger a scan |
| `admin` | Everything `writer` can, plus delete a file's record |

A request past its role (e.g. a `reader` calling `POST /scan`) gets `403
Forbidden`, naming the role actually required.

### `AUTH_MODE=jwt` (default) — self-contained, no external IdP

The app has its own basic sign-in — no external identity provider (Auth0,
Okta, Entra ID, etc.), just a self-contained JWT flow:

1. **Sign in** — `POST /auth/login` (browser form) verifies the username
   and password, then sets the JWT in an HttpOnly cookie and redirects to
   `/`. `POST /auth/token` does the same check but returns the JWT as JSON
   instead — for `curl`/Postman/API use rather than a browser.
2. **Every protected route validates the token** — `auth_jwt.get_current_user()`
   reads the JWT from either the cookie or an `Authorization: Bearer <token>`
   header, verifies its signature and expiry, and extracts the username and
   role. Unauthenticated requests to the page redirect to `/login`;
   unauthenticated API calls get a plain `401 Not authenticated`.

**Where the secrets live:** both the JWT signing key and the user list
(with PBKDF2-hashed passwords, never plaintext) are Key Vault secrets,
generated once at deploy time by `create_keyvault()` in `deploy.sh` and
`scripts/generate_users_secret.py`. Only the FastAPI managed identity can
read them (same `Key Vault Secrets User` role as the storage key above).

**Demo accounts** are defined in `config.env`'s `DEMO_USERS` (one line per
user, `username:password:role`) and printed (usernames + roles, not
passwords) at the end of `./deploy.sh`. Edit that list before you deploy to
set your own accounts.

Try it:

```bash
# Get a token
curl -X POST https://<app-url>/auth/token \
  -d "username=writer1&password=<password-from-config.env>"

# Use it
curl https://<app-url>/api/files -H "Authorization: Bearer <token-from-above>"

# Confirm who you are
curl https://<app-url>/me -H "Authorization: Bearer <token-from-above>"
```

**This is a demo pattern, not a production auth system.** It deliberately
keeps things minimal to show the mechanics (issue a JWT, validate it,
authorize by role) end to end. Before using anything like this for real,
you'd want at least: rate limiting / account lockout on login attempts,
refresh tokens and a revocation list (a leaked JWT is valid until it
expires — there's no way to invalidate one early here), secret rotation for
the JWT signing key, and a real user-management story instead of a static
list. That's exactly what the other mode below gets you.

### `AUTH_MODE=azuread` — Microsoft Entra ID (Azure-native)

Switching `AUTH_MODE` to `"azuread"` replaces all of the above with
**Azure Container Apps' built-in authentication** ("Easy Auth") backed by
Microsoft Entra ID:

- Sign-in, sign-out, and token validation (signature, issuer, audience,
  expiry) all happen at the **platform level**, in front of the container —
  this app never sees a raw token or handles a password.
- Authorization still uses the same reader/writer/admin roles, but now
  they're **Entra ID App Roles**, assigned to specific users (or groups) in
  your tenant. `fastapi_app/auth_azuread.py` just reads the identity Azure
  forwards in a request header (`X-MS-CLIENT-PRINCIPAL`) — see that file's
  docstring for exactly how.
- The client secret for the app registration is generated once, stored only
  in Key Vault, and referenced by the Container App via a Key Vault
  reference (`keyvaultref:...,identityref:...`) — never a raw value in an
  env var.

Setting this up requires registering an application in Entra ID first —
see **[`docs/azure-ad-setup.md`](docs/azure-ad-setup.md)** for the full
walkthrough and `scripts/setup_azure_ad.sh` for the automation. Short
version: set `AUTH_MODE="azuread"` in `config.env`, optionally fill in
`AAD_USER_ROLE_ASSIGNMENTS`, and run `./deploy.sh` — it detects there's no
Entra ID app yet and registers one automatically once it knows the
Container App's URL.

Both auth modes are real, working code paths in the same container image
(picked at runtime by `AUTH_MODE`) specifically so you can compare them
side by side.

### Diagrams

- **`docs/diagrams/jwt-mode-architecture.svg`** — the `AUTH_MODE=jwt` flow:
  browser to FastAPI directly, Key Vault holding the JWT secret and hashed
  user list, then Cosmos DB / the file share.
- **`docs/diagrams/azuread-mode-architecture.svg`** — the `AUTH_MODE=azuread`
  flow: browser to Container Apps' built-in auth, which redirects to Entra
  ID, validates the token using a Key Vault-held client secret, and only
  then forwards an already-authenticated request to FastAPI with the
  caller's identity in a header.
- **`docs/diagrams/future-state-architecture.svg`** — see below.

## Future state: a production-grade version of this

Everything above is a working demo, not a production deployment. If this
were headed to production, here's the shape it would take
(`docs/diagrams/future-state-architecture.svg`) — none of this is built in
this repo; it's a map of what enterprise standards would add on top:

- **Edge & API** — Azure Front Door with WAF in front of everything (DDoS
  protection, TLS termination, global routing), and an API Management
  gateway in front of the app for rate limiting, request validation, and a
  managed API surface instead of exposing the Container App directly.
- **App runtime** — Container Apps with ingress set to internal-only,
  reachable solely through the gateway above, all inside a VNet.
- **Data** — Storage, Cosmos DB, and Key Vault reachable only via private
  endpoints inside that same VNet — no public data-plane endpoints at all,
  vs. this demo's public (but managed-identity/RBAC-gated) endpoints.
- **Identity & governance** — `AUTH_MODE=azuread` as the baseline, plus
  Conditional Access policies (block legacy auth, require compliant
  devices, require MFA) and Privileged Identity Management for just-in-time
  admin role activation instead of standing Admin app-role assignments.
- **Observability** — Log Analytics + Application Insights for telemetry,
  Microsoft Sentinel for SIEM/alerting on anomalous sign-ins or access
  patterns, and Defender for Cloud for posture management across the whole
  landing zone.
- **CI/CD** — the manual `az acr build` / `deploy.sh` steps here become an
  automated pipeline: build, container image scanning, IaC (Bicep/Terraform
  instead of an imperative bash script), staged rollout, and automated
  secret rotation for the Key Vault-held values.
- Beyond the diagram: multi-region deployment with Cosmos DB's own
  multi-region replication for resilience, and a real change-management
  process instead of a single script an individual runs by hand.

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

At the end it prints the FastAPI app's public URL along with the demo
accounts (usernames and roles). Open the app, sign in as a `writer` or
`admin` account, and use the "Upload to file share" form or the "Scan file
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
fallback if the Azure Function gives you trouble. Note that both talk to
Azure directly with your own `az login` identity — they don't go through
the FastAPI app, so the JWT sign-in described above doesn't apply to them
(that's app-level authorization on top of, not instead of, Azure's own
RBAC).

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
config.env                          deployment parameters (incl. demo user list, AUTH_MODE)
deploy.sh                           deploy / destroy script (handles both auth modes)
docs/azure-ad-setup.md              prerequisites for AUTH_MODE=azuread
docs/diagrams/                      the three architecture diagrams referenced above
function_app/                       Azure Function (timer trigger, Python v2 model)
fastapi_app/                        FastAPI UI (Jinja2 templates) + scan/upload/delete endpoints
fastapi_app/clients.py              shared Key Vault / file share / Cosmos client helpers
fastapi_app/roles.py                shared reader/writer/admin role hierarchy (both auth modes)
fastapi_app/auth.py                 facade — picks auth_jwt.py or auth_azuread.py via AUTH_MODE
fastapi_app/auth_jwt.py             AUTH_MODE=jwt backend: this app's own login/JWT issuance
fastapi_app/auth_azuread.py         AUTH_MODE=azuread backend: reads Container Apps' Easy Auth header
fastapi_app/templates/login.html    sign-in page (AUTH_MODE=jwt only)
scripts/upload_to_fileshare.sh      upload local files to the file share
scripts/simulate_local_ingestion.py fallback: writes metadata to Cosmos DB without the Function
scripts/generate_users_secret.py    hashes DEMO_USERS for the Key Vault 'app-users' secret (AUTH_MODE=jwt)
scripts/setup_azure_ad.sh           registers the Entra ID app + roles (AUTH_MODE=azuread)
data/                               local scratch folder used by the scripts above
```
