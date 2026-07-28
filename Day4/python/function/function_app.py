"""
OakTree Trade Ingest — Python Azure Functions (v2 programming model).

Demonstrates the two most common trigger types side by side:
  - HTTP trigger: a lightweight endpoint (e.g. for a partner system to push
    a trade), decorated directly with @app.route — no separate function.json
    needed with the v2 model.
  - Queue trigger: consumes messages from an Azure Storage Queue, the classic
    "glue" pattern — something upstream drops a message, this function reacts.

Both share the same Key Vault + Managed Identity pattern as the API sample.
Deploy on a Consumption plan for pay-per-execution, spiky workloads, or on
Premium/Dedicated if you need VNet integration or no cold starts.
"""
import json
import logging
import os

import azure.functions as func

app = func.FunctionApp()
logger = logging.getLogger("oaktree.functions")


def _get_secret(name: str) -> str | None:
    """Same DefaultAzureCredential pattern as the API service — works
    unchanged locally (az login) and in Azure (Managed Identity)."""
    vault_url = os.getenv("KEY_VAULT_URL")
    if not vault_url:
        return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        return client.get_secret(name).value
    except Exception as exc:
        logging.warning("Key Vault secret fetch failed for %s: %s", name, exc)
        return None


@app.route(route="trade-ingest", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def trade_ingest(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP-triggered ingestion endpoint.
    Try it locally: POST http://localhost:7071/api/trade-ingest
        {"symbol": "MSFT", "quantity": 500, "side": "BUY"}
    """
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "invalid JSON body"}), status_code=400, mimetype="application/json")

    required = {"symbol", "quantity", "side"}
    if not required.issubset(body):
        missing = required - set(body)
        return func.HttpResponse(
            json.dumps({"error": f"missing fields: {sorted(missing)}"}),
            status_code=400, mimetype="application/json",
        )

    logging.info("trade ingested via HTTP: %s %s %s", body["side"], body["quantity"], body["symbol"])
    return func.HttpResponse(
        json.dumps({"status": "accepted", "trade": body}),
        status_code=202, mimetype="application/json",
    )


@app.queue_trigger(arg_name="msg", queue_name="trade-events", connection="AzureWebJobsStorage")
def trade_events_consumer(msg: func.QueueMessage) -> None:
    """Queue-triggered background processor. This is the pattern for
    decoupling ingestion from downstream processing (e.g. positions update,
    compliance check) via Azure Storage Queues or Service Bus."""
    body = msg.get_body().decode("utf-8")
    logging.info("processing queued trade event: %s", body)
    try:
        event = json.loads(body)
        logging.info("trade event processed for symbol=%s", event.get("symbol", "unknown"))
    except json.JSONDecodeError:
        logging.error("could not parse queue message as JSON: %s", body)
