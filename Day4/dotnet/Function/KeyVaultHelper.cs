using Azure.Identity;
using Azure.Security.KeyVault.Secrets;
using Microsoft.Extensions.Logging;

namespace OakTree.Function;

/// <summary>
/// Same DefaultAzureCredential pattern as the API and the Python function —
/// works unchanged locally (az login) and in Azure (Managed Identity).
/// </summary>
public static class KeyVaultHelper
{
    public static string? TryGetSecret(string secretName, ILogger logger)
    {
        var vaultUrl = Environment.GetEnvironmentVariable("KEY_VAULT_URL");
        if (string.IsNullOrWhiteSpace(vaultUrl)) return null;

        try
        {
            var client = new SecretClient(new Uri(vaultUrl), new DefaultAzureCredential());
            return client.GetSecret(secretName).Value.Value;
        }
        catch (Exception ex)
        {
            logger.LogWarning("Key Vault secret fetch failed for {SecretName}: {Message}", secretName, ex.Message);
            return null;
        }
    }
}
