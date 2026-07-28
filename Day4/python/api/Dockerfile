# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# OakTree Positions API — Python (FastAPI) — multi-stage, production-shaped
# image. Demonstrates the Day 6 containerization checklist:
#   - multi-stage build (build tools never reach the final image)
#   - slim base image
#   - non-root user
#   - only the artifacts the app needs are copied in
#   - HEALTHCHECK wired to the /healthz endpoint
# ---------------------------------------------------------------------------

# ---- Stage 1: build dependencies into a wheelhouse -------------------------
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Stage 2: slim runtime image -------------------------------------------
FROM python:3.12-slim AS runtime

# Create a non-root user to run the app (never run production containers as root)
RUN addgroup --system app && adduser --system --ingroup app appuser

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY app ./app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1

# Bind to 0.0.0.0 so the container is reachable from outside — App Service /
# Container Apps / AKS all route traffic to the container's exposed port.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
