"""
Structured logging + Azure Application Insights wiring for the OakTree
Positions API.

Two things happen here:
1. Every log line is emitted as JSON (structured logging) so Log Analytics /
   Azure Monitor can query fields directly instead of grepping free text.
2. If APPLICATIONINSIGHTS_CONNECTION_STRING is set, traces/metrics/logs are
   also exported to Application Insights via the OpenTelemetry distro —
   this is what gives you the live request map and failure/latency charts.
"""
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from .config import settings

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": correlation_id_ctx.get(),
            "service": settings.app_name,
            "environment": settings.environment,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]

    if settings.appinsights_connection_string:
        try:
            # azure-monitor-opentelemetry wires logging, tracing and metrics
            # in one call — this is the current (2024+) recommended package,
            # replacing the older opencensus-based exporters.
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=settings.appinsights_connection_string)
            root.info("Application Insights telemetry enabled.")
        except Exception as exc:
            root.warning("Application Insights configuration failed: %s", exc)
    else:
        root.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry export disabled (local mode).")

    return logging.getLogger("oaktree.api")


class CorrelationIdMiddleware:
    """Attach (or propagate) a correlation ID to every request so one trade
    can be followed across every service and every log line it touches."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        incoming = headers.get(b"x-correlation-id")
        cid = incoming.decode() if incoming else str(uuid.uuid4())
        token = correlation_id_ctx.set(cid)
        start = time.time()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"x-correlation-id", cid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.time() - start) * 1000, 2)
            logging.getLogger("oaktree.access").info(
                "request completed", extra={"durationMs": duration_ms}
            )
            correlation_id_ctx.reset(token)
