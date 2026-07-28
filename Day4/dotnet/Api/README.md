# OakTree Positions API — .NET (ASP.NET Core minimal API)

The .NET twin of `python/api` — same endpoints, same Key Vault + Managed
Identity pattern, same structured logging + Application Insights, same
retry + circuit breaker shape — so the two languages can be taught side by
side and compared directly.

> **Note on this environment:** this code was written and reviewed carefully
> against current .NET 8 / ASP.NET Core minimal API conventions, but could not
> be compiled in the sandbox that produced this kit (no .NET SDK available).
> Build it with a real .NET 8 SDK before relying on it, the same as you would
> for any new codebase — `dotnet build` will surface anything to fix.

## Run locally (no Azure required)

Requires the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0).

```bash
dotnet restore
dotnet run
```

Try it (default: `http://localhost:5000` or `http://localhost:5xxx` — check
the console output for the exact port):
```bash
curl http://localhost:5000/healthz
curl http://localhost:5000/readyz
curl -X POST http://localhost:5000/positions \
     -H "Content-Type: application/json" \
     -d '{"symbol":"MSFT","quantity":500,"side":"BUY"}'
curl http://localhost:5000/positions
curl http://localhost:5000/pricing-check   # run a few times to see retries / circuit breaker trip
```

## Run in Docker

```bash
docker build -t oaktree-positions-api-dotnet:local .
docker run -p 8080:8080 oaktree-positions-api-dotnet:local
```

## Connecting to real Azure services

Set these as environment variables (or `dotnet user-secrets` locally, App
Settings in Azure):

| Setting | Purpose |
|---|---|
| `KEY_VAULT_URL` | e.g. `https://kv-oaktree-dev.vault.azure.net/` — `AddAzureKeyVault` merges every secret into `IConfiguration` at startup |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | enables Azure Monitor / App Insights telemetry export via OpenTelemetry |
| `ENVIRONMENT` | free-text tag shown in logs (`local`, `dev`, `prod`) |

Locally, `DefaultAzureCredential` picks up your `az login` / Visual Studio /
VS Code sign-in automatically — no extra setup needed as long as you have
`Key Vault Secrets User` access on the vault.

## What to look at, in order

1. `Program.cs` — top-level composition: config, Key Vault, logging, App
   Insights, health endpoints, the minimal API routes
2. `Middleware/CorrelationIdMiddleware.cs` — correlation ID propagation
3. `Resilience/CircuitBreaker.cs` + `DownstreamService.cs` — retry + breaker
4. `Models/Position.cs` — request/response records
5. `Dockerfile` — multi-stage, non-root, healthcheck

## Deploying

See `/Code-Companion/deploy/deploy-appservice.sh` and
`/Code-Companion/deploy/deploy-containerapps.sh` — the same scripts work for
either language; only the image you build and push differs.
