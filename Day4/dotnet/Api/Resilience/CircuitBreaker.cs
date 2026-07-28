namespace OakTree.Api.Resilience;

/// <summary>
/// A minimal circuit breaker, functionally identical to the Python version in
/// python/api/app/resilience.py — kept dependency-light and easy to read for
/// a live session. In a production .NET service, prefer the battle-tested
/// Polly library (Microsoft.Extensions.Http.Resilience's
/// AddStandardResilienceHandler) which implements retry, circuit breaker,
/// timeout and bulkhead together for outbound HttpClient calls.
/// </summary>
public class CircuitBreaker
{
    private readonly int _failureThreshold;
    private readonly TimeSpan _resetAfter;
    private int _failures;
    private DateTime? _openedAt;
    private readonly object _lock = new();

    public CircuitBreaker(int failureThreshold = 3, double resetAfterSeconds = 15.0)
    {
        _failureThreshold = failureThreshold;
        _resetAfter = TimeSpan.FromSeconds(resetAfterSeconds);
    }

    public string State
    {
        get
        {
            lock (_lock)
            {
                if (_openedAt is null) return "CLOSED";
                return DateTime.UtcNow - _openedAt.Value > _resetAfter ? "HALF_OPEN" : "OPEN";
            }
        }
    }

    public void BeforeCall()
    {
        if (State == "OPEN")
            throw new DownstreamUnavailableException("circuit breaker is OPEN — failing fast");
    }

    public void RecordSuccess()
    {
        lock (_lock)
        {
            _failures = 0;
            _openedAt = null;
        }
    }

    public void RecordFailure(ILogger logger)
    {
        lock (_lock)
        {
            _failures++;
            if (_failures >= _failureThreshold)
            {
                _openedAt = DateTime.UtcNow;
                logger.LogWarning("circuit breaker OPENED after {Failures} consecutive failures", _failures);
            }
        }
    }
}

public class DownstreamUnavailableException(string message) : Exception(message);
