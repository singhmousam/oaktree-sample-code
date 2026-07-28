"""
Resilience patterns for calling a flaky downstream dependency.

Demonstrates the two patterns almost every service needs:
  - Retry with exponential backoff + jitter (via `tenacity`) for transient
    failures — a blip that will likely succeed on the next attempt.
  - A simple Circuit Breaker so that once a dependency is clearly down, we
    stop hammering it and fail fast instead of piling up latency.

In production you would typically reach for a library like `pybreaker` or
implement this at the infrastructure layer (e.g. Dapr, a service mesh, or
Polly on the .NET side — see dotnet/Api/Resilience). The implementation below
is intentionally small and dependency-light so it's easy to read and adapt
in a live training session.
"""
import logging
import random
import time
from datetime import datetime, timedelta

from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

logger = logging.getLogger("oaktree.resilience")


class DownstreamUnavailableError(Exception):
    pass


class CircuitBreaker:
    """A minimal circuit breaker: after `failure_threshold` consecutive
    failures, the circuit OPENS and calls fail fast for `reset_after` seconds
    before allowing a single HALF_OPEN trial call through."""

    def __init__(self, failure_threshold: int = 3, reset_after: float = 15.0):
        self.failure_threshold = failure_threshold
        self.reset_after = reset_after
        self._failures = 0
        self._opened_at: datetime | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "CLOSED"
        if datetime.utcnow() - self._opened_at > timedelta(seconds=self.reset_after):
            return "HALF_OPEN"
        return "OPEN"

    def before_call(self):
        if self.state == "OPEN":
            raise DownstreamUnavailableError("circuit breaker is OPEN — failing fast")

    def record_success(self):
        self._failures = 0
        self._opened_at = None

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = datetime.utcnow()
            logger.warning("circuit breaker OPENED after %d consecutive failures", self._failures)


_breaker = CircuitBreaker()


def _simulate_flaky_dependency():
    """Stands in for a call to a downstream service/database. ~30% of calls
    fail with a transient error, to make retry/circuit-breaker behaviour
    visible in a live demo."""
    if random.random() < 0.3:
        raise ConnectionError("simulated transient failure calling downstream pricing service")
    time.sleep(0.05)
    return {"status": "ok"}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.2, max=2),
    retry=retry_if_exception_type(ConnectionError),
    reraise=True,
)
def _call_with_retry():
    return _simulate_flaky_dependency()


def call_downstream_with_resilience() -> dict:
    """Public entry point used by the API layer: circuit breaker wraps the
    retrying call. If the breaker is open, we fail fast without even trying."""
    _breaker.before_call()
    try:
        result = _call_with_retry()
        _breaker.record_success()
        return result
    except Exception:
        _breaker.record_failure()
        raise
