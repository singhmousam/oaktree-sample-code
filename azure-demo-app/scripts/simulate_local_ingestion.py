#!/usr/bin/env python3
"""
Fallback / troubleshooting helper.

If the Azure Function is giving you trouble (build, deploy, or runtime
issues), this script does the same job by hand: it scans a local directory
(default ./data), computes each file's metadata, and writes it straight to
the same Cosmos DB table the Function would have used. Point the FastAPI
app's URL at it afterwards and the records show up exactly as if the
Function had processed them -- it even tags each record with
Source="local-simulator" so you can tell them apart from real Function runs.

Auth: uses DefaultAzureCredential, so on your own machine it picks up your
`az login` session automatically. Your own account needs a Cosmos DB
data-plane role to write to the table -- deploy.sh only grants that role to
the managed identities, not to you. Grant it to yourself once with:

    az cosmosdb sql role assignment create \\
      --resource-group <rg> --account-name <cosmos-account-name> \\
      --role-definition-id 00000000-0000-0000-0000-000000000002 \\
      --principal-id "$(az ad signed-in-user show --query id -o tsv)" \\
      --scope "$(az cosmosdb show -g <rg> -n <cosmos-account-name> --query id -o tsv)"

Usage:
    pip install azure-identity azure-data-tables   # one-time

    python3 scripts/simulate_local_ingestion.py \\
        --data-dir ./data \\
        --cosmos-endpoint https://<cosmos-account>.table.cosmos.azure.com:443/ \\
        --cosmos-table filemetadata

    # or export COSMOS_TABLE_ENDPOINT / COSMOS_TABLE_NAME and omit the flags

    # preview without writing anything:
    python3 scripts/simulate_local_ingestion.py --dry-run
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from mimetypes import guess_type
from pathlib import Path

TRACKING_PARTITION_KEY = "file"


def safe_row_key(filename: str) -> str:
    """Table RowKeys can't contain / \\ # ? characters."""
    key = filename
    for ch in ["/", "\\", "#", "?"]:
        key = key.replace(ch, "_")
    return key


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--data-dir", default="data", help="Local directory to scan (default: ./data)"
    )
    parser.add_argument(
        "--cosmos-endpoint",
        default=os.environ.get("COSMOS_TABLE_ENDPOINT"),
        help="Cosmos DB Table endpoint, e.g. https://<account>.table.cosmos.azure.com:443/",
    )
    parser.add_argument(
        "--cosmos-table",
        default=os.environ.get("COSMOS_TABLE_NAME", "filemetadata"),
        help="Cosmos DB table name (default: filemetadata)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without contacting Cosmos DB",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.cosmos_endpoint:
        parser.error(
            "--cosmos-endpoint is required (or set COSMOS_TABLE_ENDPOINT), "
            "unless using --dry-run"
        )
    return args


def build_entity(path: Path) -> dict:
    stat = path.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    content_type = guess_type(path.name)[0] or "unknown"
    return {
        "PartitionKey": TRACKING_PARTITION_KEY,
        "RowKey": safe_row_key(path.name),
        "FileName": path.name,
        "FileSizeBytes": stat.st_size,
        "FileType": content_type,
        "LastModified": last_modified,
        "ProcessedAt": datetime.now(timezone.utc).isoformat(),
        "Source": "local-simulator",
    }


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)

    if not data_dir.is_dir():
        sys.exit(
            f"Directory '{data_dir}' does not exist. Create it and add some "
            "files, or pass --data-dir."
        )

    files = sorted(p for p in data_dir.iterdir() if p.is_file())
    if not files:
        sys.exit(f"No files found directly inside '{data_dir}'. Add some files and re-run.")

    entities = [build_entity(p) for p in files]

    if args.dry_run:
        print(f"[dry run] Would write {len(entities)} record(s):")
        for e in entities:
            print(
                f"  - {e['FileName']}  ({e['FileSizeBytes']} bytes, "
                f"{e['FileType']}, modified {e['LastModified']})"
            )
        return

    try:
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        sys.exit(
            "Missing dependencies. Install them with:\n"
            "  pip install azure-identity azure-data-tables"
        )

    credential = DefaultAzureCredential()
    table_client = TableServiceClient(
        endpoint=args.cosmos_endpoint, credential=credential
    ).get_table_client(args.cosmos_table)

    for entity in entities:
        table_client.upsert_entity(entity)
        print(f"Wrote record for {entity['FileName']}")

    print(f"\nDone. {len(entities)} record(s) written to table '{args.cosmos_table}'.")


if __name__ == "__main__":
    main()
