using System.Text.Json;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;

namespace OakTree.Function;

/// <summary>
/// Queue-triggered background processor — the .NET twin of
/// python/function/function_app.py's trade_events_consumer function.
/// This is the pattern for decoupling ingestion from downstream processing
/// (positions update, compliance check) via Azure Storage Queues or Service Bus.
/// </summary>
public class TradeEventsConsumerFunction(ILogger<TradeEventsConsumerFunction> logger)
{
    [Function("TradeEventsConsumer")]
    public void Run(
        [QueueTrigger("trade-events", Connection = "AzureWebJobsStorage")] string message)
    {
        logger.LogInformation("processing queued trade event: {Message}", message);
        try
        {
            using var doc = JsonDocument.Parse(message);
            var symbol = doc.RootElement.TryGetProperty("symbol", out var s) ? s.GetString() : "unknown";
            logger.LogInformation("trade event processed for symbol={Symbol}", symbol);
        }
        catch (JsonException)
        {
            logger.LogError("could not parse queue message as JSON: {Message}", message);
        }
    }
}
