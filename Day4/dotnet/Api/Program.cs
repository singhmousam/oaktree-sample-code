using Azure.Identity;
using Azure.Monitor.OpenTelemetry.AspNetCore;
using OakTree.Api.Middleware;
using OakTree.Api.Models;
using OakTree.Api.Resilience;
using OpenTelemetry.Extensions.Hosting;
using Serilog;
using Serilog.Formatting.Compact;

var builder = WebApplication.CreateBuilder(args);

// ---------------------------------------------------------------------------
// Config: environment variables (12-factor) + secrets from Azure Key Vault.
// DefaultAzureCredential means: on your laptop it falls back to `az login` /
// Visual Studio / VS Code sign-in; in Azure App Service or Container Apps it
// automatically uses the resource's Managed Identity. No secrets in code.
// ---------------------------------------------------------------------------
var keyVaultUrl = builder.Configuration["KEY_VAULT_URL"];
if (!string.IsNullOrWhiteSpace(keyVaultUrl))
{
    try
    {
        builder.Configuration.AddAzureKeyVault(new Uri(keyVaultUrl), new DefaultAzureCredential());
    }
    catch (Exception ex)
    {
        Console.WriteLine($"WARNING: could not load secrets from Key Vault ({keyVaultUrl}): {ex.Message}. Using fallback config.");
    }
}

var environment = builder.Configuration["ENVIRONMENT"] ?? "local";
var appName = builder.Configuration["APP_NAME"] ?? "oaktree-positions-api";

// ---------------------------------------------------------------------------
// Structured JSON logging via Serilog. CompactJsonFormatter emits one JSON
// object per line — exactly what Log Analytics / Azure Monitor want to query.
// ---------------------------------------------------------------------------
builder.Host.UseSerilog((context, services, configuration) =>
{
    configuration
        .Enrich.FromLogContext()
        .Enrich.WithProperty("Service", appName)
        .Enrich.WithProperty("Environment", environment)
        .WriteTo.Console(new CompactJsonFormatter())
        .MinimumLevel.Information();
});

// ---------------------------------------------------------------------------
// Application Insights via OpenTelemetry — traces, metrics and logs export
// automatically once APPLICATIONINSIGHTS_CONNECTION_STRING is set.
// ---------------------------------------------------------------------------
var appInsightsConnectionString = builder.Configuration["APPLICATIONINSIGHTS_CONNECTION_STRING"];
if (!string.IsNullOrWhiteSpace(appInsightsConnectionString))
{
    builder.Services.AddOpenTelemetry().UseAzureMonitor(options =>
    {
        options.ConnectionString = appInsightsConnectionString;
    });
}

builder.Services.AddSingleton<DownstreamService>();
builder.Services.AddHealthChecks();

// Minimal APIs use System.Text.Json, which serializes enums as integers by
// default. Register the string converter so a request body of {"side":"BUY"}
// binds correctly instead of requiring {"side":0} — mirrors the Python side,
// where Pydantic's Literal["BUY","SELL"] accepts the string directly.
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.Converters.Add(new System.Text.Json.Serialization.JsonStringEnumConverter());
});

var app = builder.Build();

app.UseSerilogRequestLogging();
app.UseMiddleware<CorrelationIdMiddleware>();

// In-memory "database" for the demo — a real service would use Azure SQL or
// Cosmos DB here, with the connection string coming from Key Vault above.
var positions = new Dictionary<string, Position>();

// -------------------- Health & readiness --------------------
// Liveness: "is the process alive?" — deliberately dependency-free so a
// downstream outage never kills a healthy instance.
app.MapGet("/healthz", () => Results.Ok(new { status = "healthy", service = appName }))
   .WithTags("ops");

// Readiness: "can this instance actually serve traffic?" App Service health
// checks, Container Apps probes and Kubernetes readiness probes all call
// something like this before routing traffic to the instance.
app.MapGet("/readyz", (IConfiguration config) =>
{
    var ready = !string.IsNullOrWhiteSpace(config["DB_CONNECTION_STRING_FALLBACK"]) || true; // demo: always ready
    return ready
        ? Results.Ok(new { status = "ready", environment })
        : Results.Json(new { status = "not ready" }, statusCode: 503);
}).WithTags("ops");

// -------------------- Positions API --------------------
app.MapGet("/positions", (ILogger<Program> logger) =>
{
    logger.LogInformation("listing positions, count={Count}", positions.Count);
    return Results.Ok(positions.Values);
}).WithTags("positions");

app.MapGet("/positions/{id}", (string id) =>
    positions.TryGetValue(id, out var position) ? Results.Ok(position) : Results.NotFound(new { detail = "position not found" })
).WithTags("positions");

app.MapPost("/positions", (PositionIn input, ILogger<Program> logger) =>
{
    var position = Position.From(input);
    positions[position.Id] = position;
    logger.LogInformation("position booked, symbol={Symbol}, quantity={Quantity}", position.Symbol, position.Quantity);
    return Results.Created($"/positions/{position.Id}", position);
}).WithTags("positions");

// -------------------- Resilience demo --------------------
app.MapGet("/pricing-check", async (DownstreamService downstream, ILogger<Program> logger) =>
{
    try
    {
        var result = await downstream.CallWithResilienceAsync();
        return Results.Ok(new { downstream = result });
    }
    catch (DownstreamUnavailableException ex)
    {
        return Results.Json(new { detail = ex.Message }, statusCode: 503);
    }
    catch (Exception ex)
    {
        logger.LogError(ex, "downstream call failed after retries");
        return Results.Json(new { detail = $"downstream failed: {ex.Message}" }, statusCode: 502);
    }
}).WithTags("resilience");

app.Run();
