"""
Minimal FastAPI app that reads file-metadata records from a Cosmos DB
(Table API) table — written there by the companion Azure Function — and
renders them as an HTML table. Authenticates to Cosmos DB using the
container's user-assigned managed identity; no keys or connection strings.
"""

import os

from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

COSMOS_TABLE_ENDPOINT = os.environ["COSMOS_TABLE_ENDPOINT"]
COSMOS_TABLE_NAME = os.environ["COSMOS_TABLE_NAME"]
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")

app = FastAPI(title="Uploaded Files Demo")
templates = Jinja2Templates(directory="templates")


def _credential() -> DefaultAzureCredential:
    if CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=CLIENT_ID)
    return DefaultAzureCredential()


def _table_client():
    service = TableServiceClient(
        endpoint=COSMOS_TABLE_ENDPOINT, credential=_credential()
    )
    return service.get_table_client(COSMOS_TABLE_NAME)


def _fetch_files():
    client = _table_client()
    entities = list(client.list_entities())
    entities.sort(key=lambda e: e.get("ProcessedAt", ""), reverse=True)
    return entities


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    error = None
    files = []
    try:
        files = _fetch_files()
    except Exception as exc:  # noqa: BLE001 - surface any auth/config issue in the UI
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"files": files, "error": error}
    )


@app.get("/api/files")
def api_files():
    return {"files": _fetch_files()}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
