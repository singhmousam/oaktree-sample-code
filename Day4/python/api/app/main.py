"""
OakTree Positions API — Python (FastAPI) reference implementation.

Demonstrates, in one small runnable service, everything Day 4-9 covers:
  - A clean REST surface (App Service / Container Apps friendly)
  - Config from the environment + secrets from Azure Key Vault (Managed Identity)
  - Structured JSON logging + Application Insights telemetry
  - Liveness/readiness endpoints (what Azure health probes / Container Apps
    probes / Kubernetes probes all expect)
  - Retry + circuit breaker around a simulated downstream call

Run locally:
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

Then try:
    curl http://localhost:8000/healthz
    curl http://localhost:8000/positions
    curl -X POST http://localhost:8000/positions -H "Content-Type: application/json" \
         -d '{"symbol":"MSFT","quantity":500,"side":"BUY"}'
"""
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from .logging_setup import configure_logging, CorrelationIdMiddleware
from .resilience import call_downstream_with_resilience, DownstreamUnavailableError

logger = configure_logging()

app = FastAPI(title="OakTree Positions API", version="1.0.0")
app.add_middleware(CorrelationIdMiddleware)

# In-memory "database" for the demo — a real service would use Azure SQL /
# Cosmos DB here, with the connection string coming from settings.db_connection_string.
_positions: dict[str, dict] = {}


class PositionIn(BaseModel):
    symbol: str = Field(..., examples=["MSFT"])
    quantity: int = Field(..., gt=0)
    side: Literal["BUY", "SELL"]


class Position(PositionIn):
    id: str
    booked_at: datetime


@app.get("/healthz", tags=["ops"])
def liveness():
    """Liveness probe — 'is the process alive at all?' Kept deliberately
    dependency-free so a downstream outage never kills a healthy pod/instance."""
    return {"status": "healthy", "service": settings.app_name}


@app.get("/readyz", tags=["ops"])
def readiness():
    """Readiness probe — 'can this instance actually serve traffic?' Azure
    App Service health check, Container Apps probes and Kubernetes readiness
    probes all call an endpoint like this before routing traffic to it."""
    ready = bool(settings.db_connection_string)
    if not ready:
        raise HTTPException(status_code=503, detail="dependency configuration missing")
    return {"status": "ready", "environment": settings.environment}


@app.get("/positions", response_model=list[Position], tags=["positions"])
def list_positions():
    logger.info("listing positions", extra={"count": len(_positions)})
    return list(_positions.values())


@app.get("/positions/{position_id}", response_model=Position, tags=["positions"])
def get_position(position_id: str):
    position = _positions.get(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="position not found")
    return position


@app.post("/positions", response_model=Position, status_code=201, tags=["positions"])
def create_position(body: PositionIn):
    position = Position(id=str(uuid4()), booked_at=datetime.utcnow(), **body.model_dump())
    _positions[position.id] = position.model_dump()
    logger.info("position booked", extra={"symbol": position.symbol, "quantity": position.quantity})
    return position


@app.get("/pricing-check", tags=["resilience"])
def pricing_check():
    """Demo endpoint that exercises the retry + circuit breaker wrapper
    around a simulated flaky downstream dependency. Call this a handful of
    times in a row to see retries succeed, then the breaker trip open."""
    try:
        result = call_downstream_with_resilience()
        return {"downstream": result}
    except DownstreamUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("downstream call failed after retries", exc_info=True)
        raise HTTPException(status_code=502, detail=f"downstream failed: {exc}")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled exception", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
