using System.Net;
using System.Text.Json;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;

namespace OakTree.Function;

/// <summary>
/// HTTP-triggered ingestion endpoint — the .NET twin of
/// python/function/function_app.py's trade_ingest function.
/// Try it locally: POST http://localhost:7071/api/trade-ingest
///     {"symbol": "MSFT", "quantity": 500, "side": "BUY"}
/// </summary>
public class TradeIngestFunction(ILogger<TradeIngestFunction> logger)
{
    private static readonly string[] RequiredFields = ["symbol", "quantity", "side"];

    [Function("TradeIngest")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Function, "post", Route = "trade-ingest")] HttpRequestData req)
    {
        JsonDocument body;
        try
        {
            var requestBody = await new StreamReader(req.Body).ReadToEndAsync();
            body = JsonDocument.Parse(requestBody);
        }
        catch (JsonException)
        {
            return await WriteJson(req, HttpStatusCode.BadRequest, new { error = "invalid JSON body" });
        }

        var root = body.RootElement;
        var missing = RequiredFields.Where(f => !root.TryGetProperty(f, out _)).ToArray();
        if (missing.Length > 0)
        {
            return await WriteJson(req, HttpStatusCode.BadRequest, new { error = $"missing fields: [{string.Join(", ", missing)}]" });
        }

        logger.LogInformation(
            "trade ingested via HTTP: {Side} {Quantity} {Symbol}",
            root.GetProperty("side").GetString(),
            root.GetProperty("quantity").GetInt32(),
            root.GetProperty("symbol").GetString());

        return await WriteJson(req, HttpStatusCode.Accepted, new { status = "accepted", trade = JsonSerializer.Deserialize<object>(root.GetRawText()) });
    }

    private static async Task<HttpResponseData> WriteJson(HttpRequestData req, HttpStatusCode statusCode, object payload)
    {
        var response = req.CreateResponse(statusCode);
        response.Headers.Add("Content-Type", "application/json");
        await response.WriteStringAsync(JsonSerializer.Serialize(payload));
        return response;
    }
}
