namespace OakTree.Api.Resilience;

/// <summary>
/// Stands in for a call to a flaky downstream dependency (a pricing service,
/// a partner API, etc). Wraps a simulated call with retry-with-backoff, then
/// a circuit breaker — the same two-pattern combination as the Python sample,
/// so the two languages can be taught side by side.
/// </summary>
public class DownstreamService(ILogger<DownstreamService> logger)
{
    private static readonly CircuitBreaker Breaker = new();
    private static readonly Random Rng = new();

    public async Task<Dictionary<string, string>> CallWithResilienceAsync()
    {
        Breaker.BeforeCall();
        try
        {
            var result = await CallWithRetryAsync();
            Breaker.RecordSuccess();
            return result;
        }
        catch
        {
            Breaker.RecordFailure(logger);
            throw;
        }
    }

    private async Task<Dictionary<string, string>> CallWithRetryAsync()
    {
        const int maxAttempts = 3;
        Exception? lastError = null;

        for (var attempt = 1; attempt <= maxAttempts; attempt++)
        {
            try
            {
                return await SimulateFlakyDependencyAsync();
            }
            catch (InvalidOperationException ex) when (attempt < maxAttempts)
            {
                lastError = ex;
                // Exponential backoff with jitter: 200ms, 400ms, ... capped, plus randomness
                var backoffMs = Math.Min(200 * Math.Pow(2, attempt - 1), 2000) + Rng.Next(0, 100);
                logger.LogWarning(
                    "transient failure calling downstream (attempt {Attempt}/{Max}), retrying in {Backoff}ms",
                    attempt, maxAttempts, backoffMs);
                await Task.Delay(TimeSpan.FromMilliseconds(backoffMs));
            }
        }

        throw lastError ?? new InvalidOperationException("downstream call failed after retries");
    }

    private async Task<Dictionary<string, string>> SimulateFlakyDependencyAsync()
    {
        // ~30% simulated transient failure rate, to make retry/breaker behaviour visible live.
        if (Rng.NextDouble() < 0.3)
            throw new InvalidOperationException("simulated transient failure calling downstream pricing service");

        await Task.Delay(50);
        return new Dictionary<string, string> { ["status"] = "ok" };
    }
}
