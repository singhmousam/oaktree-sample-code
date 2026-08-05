"""
FastAPI app.

- Reads recorded file metadata from Cosmos DB (Table API) and shows it as an
  HTML table.
- "Scan file share now" — lists everything currently in the Azure File Share
  (via a key read from Key Vault) and upserts metadata for each file into
  Cosmos DB, on demand, without waiting for the Function's timer.
- File upload form / endpoint — lets a user upload a file to the file share
  directly from the browser, and immediately records its metadata too.
- Delete a record — admins only.

Everything above is protected by a self-contained JWT auth layer (see
auth.py): sign in via /login (browser, cookie-based) or /auth/token (API,
returns JSON), then every route resolves the caller's identity and role
(reader/writer/admin) from that token.

Cosmos DB access uses the container's managed identity (OAuth) directly.
File share access uses a storage account key that a managed identity reads
out of Key Vault (see clients.py for details on why).
"""

from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import auth
from clients import TRACKING_PARTITION_KEY, build_entity, get_share_client, get_table_client, iso

app = FastAPI(title="Uploaded Files Demo")
templates = Jinja2Templates(directory="templates")


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/auth/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    """Browser flow: verify credentials, set an HttpOnly cookie, redirect to /."""
    user = auth.authenticate(username, password)
    if not user:
        return RedirectResponse(url="/login?error=Invalid+username+or+password", status_code=303)

    token = auth.create_access_token(username=user["username"], role=user["role"])
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return redirect


@app.post("/auth/token")
def issue_token(username: str = Form(...), password: str = Form(...)):
    """API flow: verify credentials, return the JWT as JSON (no cookie). For curl/Postman-style use:

        curl -X POST <app-url>/auth/token -d "username=writer1&password=<pw>"
        curl <app-url>/api/files -H "Authorization: Bearer <token>"
    """
    user = auth.authenticate(username, password)
    if not user:
        return HTMLResponse(status_code=401, content='{"detail":"Invalid username or password"}')

    token = auth.create_access_token(username=user["username"], role=user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
        "expires_in_minutes": auth.ACCESS_TOKEN_EXPIRE_MINUTES,
    }


@app.get("/auth/logout")
@app.post("/auth/logout")
def logout():
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.delete_cookie("access_token")
    return redirect


@app.get("/me")
def me(user: dict = Depends(auth.get_current_user)):
    """Small endpoint to demonstrate token validation directly: returns the caller's identity/role."""
    return user


# --------------------------------------------------------------------------
# Application routes
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None, user: dict | None = Depends(auth.get_current_user_optional)):
    if user is None:
        return RedirectResponse(url="/login")

    error = None
    files = []
    try:
        files = _fetch_recorded_files()
    except Exception as exc:  # noqa: BLE001 - surface any auth/config issue in the UI
        error = str(exc)

    can_write = auth.ROLE_RANK[user["role"]] >= auth.ROLE_RANK["writer"]
    can_admin = user["role"] == "admin"

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "files": files,
            "error": error,
            "message": message,
            "user": user,
            "can_write": can_write,
            "can_admin": can_admin,
        },
    )


@app.post("/scan")
def scan_now(user: dict = Depends(auth.require_min_role("writer"))):
    try:
        count = _scan_and_record(source="fastapi-scan")
        message = f"Scan complete — {count} file(s) checked in the share."
    except Exception as exc:  # noqa: BLE001
        message = f"Scan failed: {exc}"
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(auth.require_min_role("writer"))):
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


@app.post("/files/{row_key}/delete")
def delete_file_record(row_key: str, user: dict = Depends(auth.require_min_role("admin"))):
    try:
        table_client = get_table_client()
        table_client.delete_entity(partition_key=TRACKING_PARTITION_KEY, row_key=row_key)
        message = f"Deleted record '{row_key}'."
    except Exception as exc:  # noqa: BLE001
        message = f"Delete failed: {exc}"
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.get("/api/files")
def api_files(user: dict = Depends(auth.require_min_role("reader"))):
    return {"files": _fetch_recorded_files()}


@app.get("/healthz")
def healthz():
    # Intentionally unauthenticated — standard practice for container
    # liveness/readiness probes.
    return {"status": "ok"}
