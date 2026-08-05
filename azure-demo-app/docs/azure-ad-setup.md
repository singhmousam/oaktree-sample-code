# Setting up Microsoft Entra ID as the identity provider (AUTH_MODE=azuread)

This is the prerequisite walkthrough for switching the FastAPI app from its
built-in JWT login (`AUTH_MODE=jwt`, the default) to Microsoft Entra ID
(Azure AD), using Azure Container Apps' built-in authentication ("Easy
Auth"). Read this before setting `AUTH_MODE=azuread` in `config.env`.

## Why this is a separate document/script from `deploy.sh`

Registering an application in Entra ID requires **directory-level**
permission — for example the "Application Administrator" directory role, or
a tenant setting that allows members to register apps. That's a different
permission from the **subscription/resource-group Contributor** role that
`deploy.sh` otherwise needs to create Azure resources. In many
organizations these are two different teams (identity/security vs.
infrastructure), with separate approval processes. Splitting this into its
own script lets each team run only the part they're authorized for.

`scripts/setup_azure_ad.sh` does the Entra ID part. `deploy.sh` will call it
automatically the first time you deploy with `AUTH_MODE=azuread` (it needs
the Container App's URL, which only exists once the app itself has been
created, so it runs this step right after that). If your organization
splits these responsibilities, an identity administrator can instead run
`scripts/setup_azure_ad.sh` themselves ahead of time — deploy.sh detects the
existing output and skips re-running it.

## What gets created in Entra ID

- **One app registration** (`AAD_APP_DISPLAY_NAME` in `config.env`), acting
  as both the relying party for sign-in and the resource whose App Roles
  define authorization.
- **Three App Roles**: `Reader`, `Writer`, `Admin` — these are exactly the
  reader/writer/admin roles used throughout this app; assigning a user one
  of these roles in Entra ID is what grants (or doesn't grant) them access.
- **A client secret**, valid 1 year, stored only in Key Vault (see below) —
  never in `config.env`, source control, or a plain container app setting.
- **App role assignments** for whichever users you listed in
  `config.env`'s `AAD_USER_ROLE_ASSIGNMENTS`, if any.

Nobody gets in by default. Being a valid user in your tenant is not
enough — Entra ID confirms *who* signed in, but the app additionally
requires that user to have one of the three App Roles assigned, exactly
like the "limited user list" the JWT mode enforces with its own user store.

## Prerequisites

- An Entra ID tenant you can register applications in (most "Contributor
  only" subscription access is *not* enough by itself — check with your
  identity team if `az ad app create` fails with a permissions error).
- Azure CLI logged in as a user who can register apps (`az login`).
- The Azure Container Apps CLI extension (`deploy.sh` installs this for
  you: `az extension add --name containerapp`).

## One-time setup

### Option A — let `deploy.sh` do it for you (simplest)

1. Set `AUTH_MODE="azuread"` in `config.env`.
2. Fill in `AAD_USER_ROLE_ASSIGNMENTS` with real users from your tenant (or
   leave it empty and assign users afterward in the Portal — see below).
3. Run `./deploy.sh` as usual. After the Container App is created, it will:
   - Detect there's no `.azuread_state` file yet
   - Run `scripts/setup_azure_ad.sh` for you, passing in the Container
     App's now-known URL as the redirect URI
   - Store the resulting client secret in Key Vault
   - Turn on Entra ID authentication on the Container App

### Option B — run it yourself first (separate teams / approval flow)

1. You won't know the Container App's URL yet on a first-ever deploy. Two
   ways to get it:
   - Deploy once with `AUTH_MODE="jwt"` (or any placeholder), note the
     printed FastAPI URL, then switch `AUTH_MODE` to `"azuread"` and
     continue below; or
   - If you're re-running against an existing deployment, get the URL with:
     `az containerapp show -g <rg> -n <container-app-name> --query properties.configuration.ingress.fqdn -o tsv`
2. Run:
   ```bash
   ./scripts/setup_azure_ad.sh \
     --redirect-uri "https://<app-fqdn>/.auth/login/aad/callback" \
     --display-name "my-demo-auth"
   ```
3. This writes `.azuread_state` (App ID, tenant ID, client secret, and the
   three role GUIDs) in the project directory. **This file contains a
   secret — it's already in `.gitignore`; don't commit it.**
4. Set `AUTH_MODE="azuread"` in `config.env` and run `./deploy.sh`.
   `deploy.sh` finds the existing `.azuread_state` and uses it directly
   instead of re-running the Entra ID setup.

## Assigning users to roles

If you filled in `AAD_USER_ROLE_ASSIGNMENTS` in `config.env`, the setup
script assigns those users automatically (via Microsoft Graph). To add or
change assignments later, either:

- **Portal**: Entra ID > Enterprise applications > *(your app's display
  name)* > Users and groups > Add user/group, then pick the Reader, Writer,
  or Admin role.
- **Re-run the script**: fill in more lines in `AAD_USER_ROLE_ASSIGNMENTS`
  and run `./scripts/setup_azure_ad.sh --redirect-uri <same-uri-as-before> --force`
  (this reuses the same app registration; `--force` just re-reads the user
  list and adds any new assignments).

Group-based assignment (assign a whole security group to a role instead of
individual users) is also supported by Entra ID App Roles, but isn't
automated by this script — do it in the Portal the same way, choosing a
group instead of a user, if you'd rather manage access that way.

## How the app validates the login (no code changes needed on your part)

Azure Container Apps' built-in authentication sits in front of the
container itself: it intercepts every request, redirects unauthenticated
browsers to Entra ID to sign in, validates the resulting token itself
(signature, issuer, audience, expiry), and only then forwards the request
to the container — with the caller's identity and app roles attached in a
request header (`X-MS-CLIENT-PRINCIPAL`). `fastapi_app/auth_azuread.py`
just reads that header; it never has to fetch Entra ID's signing keys or
validate a JWT itself, because the platform already did that. This header
can't be spoofed by an external caller — Container Apps strips or
overwrites any client-supplied copy of it before your code ever sees the
request.

## Testing the API directly (not just the browser)

You can also acquire a token for this app and call the API the same way
you would with the JWT mode's `/auth/token`, just sourced from Entra ID
instead:

```bash
# Device code flow, useful for a quick manual test:
az login --scope "api://<AAD_APP_ID>/.default" 2>/dev/null
TOKEN=$(az account get-access-token --resource "<AAD_APP_ID>" --query accessToken -o tsv)
curl https://<app-url>/api/files -H "Authorization: Bearer $TOKEN"
```

(Exact acquisition steps vary based on how your tenant is configured —
MSAL client libraries are the more typical way to do this from real client
code; the above is just enough to smoke-test the deployment.)

## Rolling back to the built-in JWT login

Set `AUTH_MODE="jwt"` in `config.env` and run `./deploy.sh` again. It
re-creates the JWT signing key and hashed demo users in Key Vault (if they
aren't already there) and updates the Container App's env vars — it does
not disable Entra ID auth on the Container App for you, since the two can
happily coexist (Easy Auth would just keep gating access in front of an app
that would otherwise use its own JWT check). To fully turn Entra ID auth
back off:

```bash
az containerapp auth update -g <rg> -n <container-app-name> --enabled false
```

## Cleanup

`./deploy.sh --destroy` removes the Azure resources but does **not** delete
the Entra ID app registration (it lives outside the resource group, in
Entra ID, and deleting app registrations automatically as part of a demo
teardown felt like the wrong default). Remove it yourself when you're done:

```bash
az ad app delete --id "$(grep AAD_APP_ID .azuread_state | cut -d= -f2)"
rm -f .azuread_state
```
