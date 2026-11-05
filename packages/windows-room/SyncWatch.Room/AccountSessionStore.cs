using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SyncWatch.Room;

/// <summary>Versioned refresh checkpoint. Deliberately excludes access tokens, names and emails.</summary>
public sealed class AccountSavedSession
{
    public string Service { get; }
    internal string AccountId { get; }
    internal string RefreshToken { get; }
    internal string RequestId { get; }
    internal AccountSavedSession(string service, string accountId, string refreshToken, string requestId)
    {
        if (Encoding.UTF8.GetByteCount(service) > 256) throw new RoomProtocolException("malformed");
        _ = new RoomAddress(service);
        foreach (string value in new[] { accountId, refreshToken, requestId })
            if (value.Length != 43 || value.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not ('_' or '-'))) throw new RoomProtocolException("malformed");
        Service = service; AccountId = accountId; RefreshToken = refreshToken; RequestId = requestId;
    }
    public static AccountSavedSession Decode(byte[] bytes)
    {
        if (bytes.Length is < 1 or > 2048) throw new RoomProtocolException("malformed");
        using JsonDocument json = RoomWire.Parse(bytes);
        JsonElement value = json.RootElement;
        string[] fields = value.EnumerateObject().Select(p => p.Name).ToArray();
        if (fields.Length != 5 || !fields.ToHashSet().SetEquals(["v", "service", "accountId", "refreshToken", "requestId"]) ||
            !value.GetProperty("v").TryGetInt32(out int version) || version != 1) throw new RoomProtocolException("malformed");
        return new(value.GetProperty("service").GetString()!, value.GetProperty("accountId").GetString()!,
            value.GetProperty("refreshToken").GetString()!, value.GetProperty("requestId").GetString()!);
    }
    internal byte[] Encode() => JsonSerializer.SerializeToUtf8Bytes(new { v = 1, service = Service, accountId = AccountId, refreshToken = RefreshToken, requestId = RequestId });
    public override string ToString() => "[saved account redacted]";
}

/// <summary>Exclusive per-profile ownership and OS credential storage; the lease holds no account data.</summary>
public sealed class AccountSessionStore(string directory) : IDisposable
{
    private readonly object gate = new();
    private FileStream? lease;
    private AccountCredentialVault? storage;
    private bool closed;
    private AccountCredentialVault Vault()
    {
        if (closed) throw new AccountVaultException();
        if (storage is not null) return storage;
        string path = Path.GetFullPath(directory);
        Directory.CreateDirectory(path);
        string file = Path.Combine(path, "account-session.lock");
        if (File.Exists(file) && File.GetAttributes(file).HasFlag(FileAttributes.ReparsePoint)) throw new AccountVaultException();
        FileStream opened = new(file, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        try
        {
            storage = new AccountCredentialVault(Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(path))));
            lease = opened; return storage;
        }
        catch { opened.Dispose(); throw; }
    }
    public AccountSavedSession? Read() { lock (gate) { byte[]? bytes = Vault().Read(); return bytes is null ? null : AccountSavedSession.Decode(bytes); } }
    public void Save(AccountSavedSession record) { lock (gate) Vault().Write(record.Encode()); }
    public void Forget() { lock (gate) Vault().Remove(); }
    public void Dispose() { lock (gate) { closed = true; storage = null; lease?.Dispose(); lease = null; } }
}
