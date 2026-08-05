"""
Timer-triggered Azure Function.

Azure Files does not emit native upload events the way Blob Storage does
(no Event Grid support for file shares), so this function polls the share
on a schedule, detects files that are new or have changed since the last
scan, and records their metadata (name, upload/modified time, size, type)
into a Cosmos DB table. All access uses the function's user-assigned
managed identity — no connection strings or account keys anywhere.
"""

import logging
import os
from datetime import datetime, timezone
from mimetypes import guess_type

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential
from azure.storage.fileshare import ShareServiceClient

app = func.FunctionApp()

STORAGE_ACCOUNT_NAME = os.environ["STORAGE_ACCOUNT_NAME"]
FILE_SHARE_NAME = os.environ["FILE_SHARE_NAME"]
COSMOS_TABLE_ENDPOINT = os.environ["COSMOS_TABLE_ENDPOINT"]
COSMOS_TABLE_NAME = os.environ["COSMOS_TABLE_NAME"]
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")  # user-assigned identity's client id

TRACKING_PARTITION_KEY = "file"


def _credential() -> DefaultAzureCredential:
    if CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=CLIENT_ID)
    return DefaultAzureCredential()


def _safe_row_key(filename: str) -> str:
    """Table RowKeys can't contain / \\ # ? characters."""
    bad_chars = ["/", "\\", "#", "?"]
    key = filename
    for ch in bad_chars:
        key = key.replace(ch, "_")
    return key


def _iso(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@app.function_name(name="ScanFileShare")
@app.timer_trigger(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def scan_file_share(timer: func.TimerRequest) -> None:
    credential = _credential()

    # token_intent is required by the SDK whenever a token/OAuth credential
    # (rather than an account key or SAS) is used to talk to Azure Files.
    share_client = ShareServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT_NAME}.file.core.windows.net",
        credential=credential,
        token_intent="backup",
    ).get_share_client(FILE_SHARE_NAME)

    table_client = TableServiceClient(
        endpoint=COSMOS_TABLE_ENDPOINT, credential=credential
    ).get_table_client(COSMOS_TABLE_NAME)

    root_dir = share_client.get_directory_client("")
    new_or_updated = 0
    scanned = 0

    for item in root_dir.list_directories_and_files():
        if item["is_directory"]:
            continue

        scanned += 1
        file_name = item["name"]
        file_client = share_client.get_file_client(file_name)
        props = file_client.get_file_properties()

        last_modified_iso = _iso(props.last_modified)
        size_bytes = props.size
        content_type = (
            props.content_settings.content_type
            if props.content_settings and props.content_settings.content_type
            else (guess_type(file_name)[0] or "unknown")
        )
        row_key = _safe_row_key(file_name)

        existing = None
        try:
            existing = table_client.get_entity(TRACKING_PARTITION_KEY, row_key)
        except ResourceNotFoundError:
            pass

        if existing is not None and existing.get("LastModified") == last_modified_iso:
            continue  # already recorded, nothing changed

        entity = {
            "PartitionKey": TRACKING_PARTITION_KEY,
            "RowKey": row_key,
            "FileName": file_name,
            "FileSizeBytes": size_bytes,
            "FileType": content_type,
            "LastModified": last_modified_iso,
            "ProcessedAt": datetime.now(timezone.utc).isoformat(),
        }
        table_client.upsert_entity(entity)
        new_or_updated += 1

    logging.info(
        "ScanFileShare complete: scanned=%s new_or_updated=%s", scanned, new_or_updated
    )
