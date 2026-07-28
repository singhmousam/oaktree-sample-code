# OakTree Trade Ingest — Python Azure Functions

Demonstrates an HTTP-triggered function and a queue-triggered function side
by side, using the Functions v2 programming model (decorators, no
`function.json` needed).

## Run locally

Requires [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local) and Azurite (or a real Storage account) for the queue trigger.

```bash
cp local.settings.json.example local.settings.json
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
func start
```

Try the HTTP trigger:
```bash
curl -X POST http://localhost:7071/api/trade-ingest \
     -H "Content-Type: application/json" \
     -d '{"symbol":"MSFT","quantity":500,"side":"BUY"}'
```

Drop a message on the `trade-events` queue (via Azurite/Storage Explorer or
the Azure CLI) to see `trade_events_consumer` fire.

## Choosing a hosting plan

| Plan | Use when |
|---|---|
| **Consumption** | Spiky/event-driven load, want scale-to-zero, cost = per execution |
| **Premium** | Need VNet integration, no cold starts, longer execution |
| **Dedicated (App Service plan)** | Already have App Service capacity to share, predictable load |

## Deploying

```bash
func azure functionapp publish <function-app-name>
```

See `/Code-Companion/deploy/deploy-functions.sh` for the full `az cli`
provisioning + deploy script, including Key Vault access via Managed Identity.
