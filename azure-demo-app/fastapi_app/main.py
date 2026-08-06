"""
FastAPI app.

- Reads recorded file metadata from Cosmos DB (Table API) and shows it as an
  HTML table (as before).
- NEW: "Scan file share now" — lists everything currently in the Azure File
  Share (via a key read from Key Vault) and upserts metadata for each file
  into Cosmos DB, on demand, without waiting for the Function's timer.
- NEW: file upload form / endpoint — lets a user upload a file to the file
  share directly from the browser, and immediately records its metadata too.

Cosmos DB access uses the container's managed identity (OAuth) directly.
File share access uses a storage account key that a managed identity reads
out of Key Vault (see clients.py for details on why).
"""

from urllib.parse import quote

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from clients import build_entity, get_share_client, get_table_client, iso

app = FastAPI(title="Uploaded Files Demo")
templates = Jinja2Templates(directory="templates")


def _fetch_recorded_files():
    client = get_table_client()
    entities = list(client.list_entities())
    entities.sort(key=lambda e: e.get("ProcessedAt", ""), reverse=True)
    return entities


def _scan_and_record(source: str = "fastapi-scan") -> int:
    """List everything currently in the file share and upsert metadata into Cosmos."""
    share_client = get_share_client()
    table_client = get_table_client()
    root_dir = share_client.get_directory_client("")

    count = 0
    for item in root_dir.list_directories_and_files():
        if item["is_directory"]:
            continue
        file_client = share_client.get_file_client(item["name"])
        props = file_client.get_file_properties()
        entity = build_entity(
            file_name=item["name"],
            size=props.size,
            last_modified=iso(props.last_modified),
            content_type=props.content_settings.content_type if props.content_settings else None,
            source=source,
        )
        table_client.upsert_entity(entity)
        count += 1
    return count


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None):
    error = None
    files = []
    try:
        files = _fetch_recorded_files()
    except Exception as exc:  # noqa: BLE001 - surface any auth/config issue in the UI
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"files": files, "error": error, "message": message},
    )


@app.post("/scan")
def scan_now():
    try:
        count = _scan_and_record(source="fastapi-scan")
        message = f"Scan complete — {count} file(s) checked in the share."
    except Exception as exc:  # noqa: BLE001
        message = f"Scan failed: {exc}"
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        share_client = get_share_client()
        contents = await file.read()

        file_client = share_client.get_file_client(file.filename)
        file_client.upload_file(contents)

        # Record metadata immediately so it shows up without waiting on the
        # Function's timer or a manual scan.
        props = file_client.get_file_properties()
        table_client = get_table_client()
        entity = build_entity(
            file_name=file.filename,
            size=props.size,
            last_modified=iso(props.last_modified),
            content_type=file.content_type,
            source="fastapi-upload",
        )
        table_client.upsert_entity(entity)
        message = f"Uploaded '{file.filename}' successfully."
    except Exception as exc:  # noqa: BLE001
        message = f"Upload failed: {exc}"
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.get("/api/files")
def api_files():
    return {"files": _fetch_recorded_files()}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
