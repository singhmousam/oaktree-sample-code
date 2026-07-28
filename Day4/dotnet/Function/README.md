# OakTree Trade Ingest — .NET Azure Functions (isolated worker model)

The .NET twin of `python/function` — an HTTP-triggered function and a
queue-triggered function, using the current **isolated worker process**
model (the model Microsoft recommends for all new .NET Functions; the older
in-process model is on a deprecation path).

> **Note on this environment:** written and reviewed against current .NET 8
> isolated-worker Azure Functions conventions, but could not be compiled here
> (no .NET SDK / Functions Core Tools available in this sandbox). Build with
> `dotnet build` and run with `func start` in a real environment before relying
> on it, same as any new codebase.

## Run locally

Requires the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0),
[Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local),
and Azurite (or a real Storage account) for the queue trigger.

```bash
cp local.settings.json.example local.settings.json
dotnet restore
func start
```

Try the HTTP trigger:
```bash
curl -X POST http://localhost:7071/api/trade-ingest \
     -H "Content-Type: application/json" \
     -d '{"symbol":"MSFT","quantity":500,"side":"BUY"}'
```

Drop a message on the `trade-events` queue (Azurite/Storage Explorer or the
Azure CLI) to see `TradeEventsConsumer` fire.

## What to look at

- `Program.cs` — isolated-worker host bootstrap + Application Insights wiring
- `TradeIngestFunction.cs` — HTTP trigger
- `TradeEventsConsumerFunction.cs` — Storage Queue trigger
- `KeyVaultHelper.cs` — same `DefaultAzureCredential` pattern used everywhere else in this kit

## Deploying

```bash
func azure functionapp publish <function-app-name>
```

See `/Code-Companion/deploy/deploy-functions.sh` for the full `az cli`
provisioning + deploy script (works for either language — the flag
`--runtime dotnet-isolated` vs `--runtime python` is the only difference).
