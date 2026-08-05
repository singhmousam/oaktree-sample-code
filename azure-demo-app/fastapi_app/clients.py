"""
Shared Azure client helpers for the FastAPI app.

Cosmos DB access still uses the container's managed identity directly
(OAuth). File share access instead goes through Key Vault: the app reads
the storage account key from a Key Vault secret (access to Key Vault itself
is via managed identity) and uses that key to talk to the file share. This
is the pattern requested for the app's new scan/upload features, as
distinct from the Function App's file-share access, which uses OAuth
directly against the storage account.
"""

import os
from datetime import datetime, timezone
from mimetypes import guess_type

from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.fileshare import ShareServiceClient

CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
KEY_VAULT_URL = os.environ["KEY_VAULT_URL"]
STORAGE_ACCOUNT_NAME = os.environ["STORAGE_ACCOUNT_NAME"]
FILE_SHARE_NAME = os.environ["FILE_SHARE_NAME"]
STORAGE_KEY_SECRET_NAME = os.environ.get("STORAGE_KEY_SECRET_NAME", "storage-account-key")
COSMOS_TABLE_ENDPOINT = os.environ["COSMOS_TABLE_ENDPOINT"]
COSMOS_TABLE_NAME = os.environ["COSMOS_TABLE_NAME"]

TRACKING_PARTITION_KEY = "file"

_storage_key_cache: str | None = None


def _credential() -> DefaultAzureCredential:
    if CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=CLIENT_ID)
    return DefaultAzureCredential()


def get_secret_client() -> SecretClient:
    return SecretClient(vault_url=KEY_VAULT_URL, credential=_credential())


def get_storage_account_key() -> str:
    """Fetch the storage account key from Key Vault, caching it in-process."""
    global _storage_key_cache
    if _storage_key_cache is None:
        secret = get_secret_client().get_secret(STORAGE_KEY_SECRET_NAME)
        _storage_key_cache = secret.value
    return _storage_key_cache


def get_share_client():
    """File share client authenticated with the key held in Key Vault."""
    account_key = get_storage_account_key()
    service_client = ShareServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT_NAME}.file.core.windows.net",
        credential=account_key,
    )
    return service_client.get_share_client(FILE_SHARE_NAME)


def get_table_client():
    """Cosmos DB Table client authenticated with the app's managed identity."""
    service_client = TableServiceClient(
        endpoint=COSMOS_TABLE_ENDPOINT, credential=_credential()
    )
    return service_client.get_table_client(COSMOS_TABLE_NAME)


def safe_row_key(filename: str) -> str:
    """Table RowKeys can't contain / \\ # ? characters."""
    key = filename
    for ch in ["/", "\\", "#", "?"]:
        key = key.replace(ch, "_")
    return key


def iso(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def build_entity(file_name: str, size: int, last_modified: str, content_type: str | None, source: str) -> dict:
    return {
        "PartitionKey": TRACKING_PARTITION_KEY,
        "RowKey": safe_row_key(file_name),
        "FileName": file_name,
        "FileSizeBytes": size,
        "FileType": content_type or guess_type(file_name)[0] or "unknown",
        "LastModified": last_modified,
        "ProcessedAt": datetime.now(timezone.utc).isoformat(),
        "Source": source,
    }
