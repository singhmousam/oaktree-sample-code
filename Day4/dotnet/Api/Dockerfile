# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# OakTree Positions API — .NET (ASP.NET Core minimal API) — multi-stage,
# production-shaped image. Same Day-6 checklist as the Python image:
#   - multi-stage build (SDK never reaches the final image)
#   - slim ASP.NET runtime base
#   - non-root user
#   - HEALTHCHECK wired to /healthz
# ---------------------------------------------------------------------------

# ---- Stage 1: build & publish ----------------------------------------------
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY OakTree.Api.csproj .
RUN dotnet restore OakTree.Api.csproj
COPY . .
RUN dotnet publish OakTree.Api.csproj -c Release -o /app/publish --no-restore

# ---- Stage 2: slim runtime image -------------------------------------------
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app

# The base runtime image doesn't ship curl/wget — install curl only for the
# HEALTHCHECK below. (Azure App Service / Container Apps / AKS actually probe
# the endpoint via their own mechanism regardless of this Docker HEALTHCHECK;
# it's included here so `docker ps` also reflects container health locally.)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Run as the built-in non-root "app" user rather than root.
RUN adduser --disabled-password --gecos "" appuser
USER appuser

COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/healthz || exit 1

ENTRYPOINT ["dotnet", "OakTree.Api.dll"]
