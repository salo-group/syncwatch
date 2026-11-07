namespace SyncWatch.RoomMetadata.Accounts;

/// <summary>Process-local authorization identity for ephemeral room ownership; not a bearer credential.</summary>
public sealed class AccountPrincipal
{
    public string AccountId { get; }
    public string SessionId { get; }
    internal AccountPrincipal(string accountId, string sessionId) { AccountId = accountId; SessionId = sessionId; }
    public override string ToString() => "[account authority redacted]";
}
