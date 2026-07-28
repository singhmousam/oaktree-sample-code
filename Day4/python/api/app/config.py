"""
Configuration loader for the OakTree Positions API.

Demonstrates the standard Azure pattern: settings come from the environment
(12-factor), and secrets are pulled from Key Vault at startup using
DefaultAzureCredential — which means:
  - On your laptop:      falls back to `az login` / VS Code / env vars
  - In Azure App Service: uses the app's system-assigned Managed Identity
No connection strings or keys are ever hard-coded or stored in the image.
"""
import os
import logging

logger = logging.getLogger("oaktree.config")


class Settings:
    def __init__(self):
        # Plain config — fine to keep as environment variables (12-factor).
        self.app_name = os.getenv("APP_NAME", "oaktree-positions-api")
        self.environment = os.getenv("ENVIRONMENT", "local")

        # Key Vault is optional for local development. In Azure, set
        # KEY_VAULT_URL as an App Setting, e.g. https://kv-oaktree.vault.azure.net/
        self.key_vault_url = os.getenv("KEY_VAULT_URL", "")

        # Application Insights connection string — usually itself pulled from
        # Key Vault in production, but App Insights also supports reading it
        # directly from an App Setting.
        self.appinsights_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

        # The "secret" this demo fetches from Key Vault to prove the wiring
        # works end to end. In a real service this would be a DB connection
        # string, an API key for a downstream partner, etc.
        self.db_connection_string = os.getenv("DB_CONNECTION_STRING_FALLBACK", "sqlite:///local.db")

        if self.key_vault_url:
            self._load_from_key_vault()

    def _load_from_key_vault(self):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self.key_vault_url, credential=credential)

            # Secret names in Key Vault use dashes, not underscores/dots.
            secret = client.get_secret("db-connection-string")
            self.db_connection_string = secret.value
            logger.info("Loaded db-connection-string from Key Vault (%s)", self.key_vault_url)

            if not self.appinsights_connection_string:
                try:
                    ai_secret = client.get_secret("appinsights-connection-string")
                    self.appinsights_connection_string = ai_secret.value
                except Exception:
                    pass  # optional — App Insights can also come from an App Setting directly
        except Exception as exc:
            # Never crash the app just because Key Vault isn't reachable in a
            # dev sandbox — log loudly and fall back to the local default.
            logger.warning("Could not load secrets from Key Vault (%s): %s. Using fallback config.", self.key_vault_url, exc)


settings = Settings()
