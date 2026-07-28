# OakTree Positions API — Python (FastAPI)

A small, runnable reference service used across Days 4, 6 and 9 to demonstrate:
cloud-native app structure, Key Vault + Managed Identity config, structured
logging + Application Insights, containerization, and resilience patterns.

## Run locally (no Azure required)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Try it:
```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl -X POST http://localhost:8000/positions \
     -H "Content-Type: application/json" \
     -d '{"symbol":"MSFT","quantity":500,"side":"BUY"}'
curl http://localhost:8000/positions
curl http://localhost:8000/pricing-check   # run a few times to see retries / circuit breaker trip
```

Interactive API docs: http://localhost:8000/docs

## Run in Docker

```bash
docker build -t oaktree-positions-api:local .
docker run -p 8000:8000 oaktree-positions-api:local
```

## Connecting to real Azure services

Set these environment variables (locally via `export`, in Azure via App
Settings) to switch on the Azure-backed behaviour:

| Variable | Purpose |
|---|---|
| `KEY_VAULT_URL` | e.g. `https://kv-oaktree-dev.vault.azure.net/` — enables Key Vault secret loading via `DefaultAzureCredential` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | enables Azure Monitor / App Insights telemetry export |
| `ENVIRONMENT` | free-text tag shown in logs (`local`, `dev`, `prod`) |

Locally, `DefaultAzureCredential` will pick up your `az login` session
automatically — no extra setup needed as long as you have `Key Vault Secrets
User` access on the vault.

## What to look at, in order

1. `app/config.py` — settings from the environment + Key Vault secret fetch
2. `app/logging_setup.py` — JSON structured logging + correlation ID + App Insights
3. `app/resilience.py` — retry with backoff + a minimal circuit breaker
4. `app/main.py` — the API surface, health/readiness endpoints, wiring it together
5. `Dockerfile` — multi-stage, non-root, healthcheck

## Deploying

See `/Code-Companion/deploy/deploy-appservice.sh` and
`/Code-Companion/deploy/deploy-containerapps.sh` for end-to-end `az cli`
scripts that provision the supporting services (Key Vault, ACR, Log
Analytics, Application Insights) and deploy this exact app.
