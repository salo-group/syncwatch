using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.DataProtection;

namespace SyncWatch.RoomMetadata.Accounts;

/// <summary>Bounded, single-writer encrypted store. Mutations become visible after flushed replacement.</summary>
public sealed class AccountRepository : IDisposable
{
    private sealed record User(string Issuer, string Subject, AccountProfile Profile);
    private sealed record Device(string UserId, string AccessHash, string RefreshHash, DateTimeOffset AccessUntil,
        DateTimeOffset RefreshUntil, string? PreviousHash = null, string? RetryId = null,
        DateTimeOffset? RetryUntil = null, DeviceSession? RetryReply = null, string[]? RetiredHashes = null)
    {
        // Rooms never survive a service restart. Rotation and pruning retain this identity;
        // deserialization creates a fresh one without migrating the encrypted account format.
        [JsonIgnore] public string RuntimeId { get; init; } = AccountWire.Secret();
    }
    private sealed record State(int Version, User[] Users, Device[] Devices);
    private readonly object gate = new();
    private readonly string path;
    private readonly IDataProtector protector;
    private readonly TimeProvider clock;
    private readonly FileStream lease;
    private State state;
    private const int MaximumBytes = 8 * 1024 * 1024;

    public AccountRepository(string directory, IDataProtectionProvider protection, TimeProvider? clock = null)
    {
        this.clock = clock ?? TimeProvider.System;
        System.IO.Directory.CreateDirectory(directory);
        lease = new FileStream(Path.Combine(directory, "writer.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        path = Path.Combine(directory, "accounts.protected");
        protector = protection.CreateProtector("SyncWatch.Accounts", "v2");
        try
        {
            if (!File.Exists(path)) state = new(2, [], []);
            else
            {
                AccountWire.Need(new FileInfo(path).Length <= MaximumBytes, "accountStoreInvalid", 503);
                state = JsonSerializer.Deserialize<State>(protector.Unprotect(File.ReadAllBytes(path)), AccountWire.Json)!;
                AccountWire.Need(state is not null && state.Version == 2 && state.Users.Length <= 10000 && state.Devices.Length <= 10000
                    && state.Users.Select(u => u.Profile.Id).Distinct().Count() == state.Users.Length
                    && state.Users.Select(u => (u.Issuer, u.Subject)).Distinct().Count() == state.Users.Length
                    && state.Devices.All(d => state.Users.Any(u => u.Profile.Id == d.UserId)), "accountStoreInvalid", 503);
            }
        }
        catch { lease.Dispose(); throw; }
    }

    public DeviceSession SignIn(string subject, string name, string email)
    {
        AccountWire.Need(AccountWire.Text(subject, 255) && AccountWire.Text(name, 128) && AccountWire.Text(email, 320), "providerIdentityInvalid", 502);
        lock (gate)
        {
            State current = Prune();
            User? existing = current.Users.SingleOrDefault(user => user.Issuer == "https://accounts.google.com" && user.Subject == subject);
            AccountProfile profile = new(existing?.Profile.Id ?? AccountWire.Secret(), name, email);
            User user = new("https://accounts.google.com", subject, profile);
            DeviceSession reply = new(profile, AccountWire.Secret(), AccountWire.Secret());
            DateTimeOffset now = clock.GetUtcNow();
            Device device = new(profile.Id, AccountWire.Hash(reply.AccessToken), AccountWire.Hash(reply.RefreshToken), now.AddMinutes(10), now.AddDays(30));
            Save(new(2, [.. current.Users.Where(u => u.Profile.Id != profile.Id), user], [.. current.Devices, device]));
            return reply;
        }
    }

    public AccountProfile Authenticate(string access)
    {
        lock (gate)
        {
            Device device = AuthenticatedDevice(access);
            return state.Users.Single(u => u.Profile.Id == device.UserId).Profile;
        }
    }

    public AccountPrincipal Authorize(string access)
    {
        lock (gate)
        {
            Device device = AuthenticatedDevice(access);
            return new(device.UserId, device.RuntimeId);
        }
    }

    public AccountProfile Profile(AccountPrincipal principal)
    {
        lock (gate)
        {
            AccountWire.Need(IsActive(principal), "unauthorized", 401);
            return state.Users.Single(user => user.Profile.Id == principal.AccountId).Profile;
        }
    }

    public bool IsActive(AccountPrincipal principal)
    {
        lock (gate)
        {
            DateTimeOffset now = clock.GetUtcNow();
            return state.Devices.Any(d => d.RuntimeId == principal.SessionId && d.UserId == principal.AccountId
                && d.AccessUntil > now && d.RefreshUntil > now);
        }
    }

    private Device AuthenticatedDevice(string access)
    {
        AccountWire.Need(AccountWire.Token(access), "unauthorized", 401);
        DateTimeOffset now = clock.GetUtcNow();
        Device? device = state.Devices.FirstOrDefault(d => AccountWire.Matches(access, d.AccessHash)
            && d.AccessUntil > now && d.RefreshUntil > now);
        AccountWire.Need(device is not null, "unauthorized", 401);
        return device;
    }

    public DeviceSession Refresh(string refresh, string requestId)
    {
        AccountWire.Need(AccountWire.Token(refresh) && AccountWire.Token(requestId), "unauthorized", 401);
        lock (gate)
        {
            State current = Prune();
            Device? device = current.Devices.FirstOrDefault(d => AccountWire.Matches(refresh, d.RefreshHash)
                || (d.RetiredHashes ?? []).Any(hash => AccountWire.Matches(refresh, hash)));
            AccountWire.Need(device is not null, "unauthorized", 401);
            DateTimeOffset now = clock.GetUtcNow();
            if (!AccountWire.Matches(refresh, device!.RefreshHash))
            {
                if (device.PreviousHash is string previous && AccountWire.Matches(refresh, previous)
                    && device.RetryId == requestId && device.RetryUntil > now && device.RetryReply is not null) return device.RetryReply;
                Save(current with { Devices = current.Devices.Where(d => d != device).ToArray() });
                throw new AccountException("sessionRevoked", 401);
            }
            if (device.RetiredHashes is { Length: >= 4096 })
            {
                Save(current with { Devices = current.Devices.Where(d => d != device).ToArray() });
                throw new AccountException("sessionRevoked", 401);
            }
            DeviceSession reply = new(current.Users.Single(u => u.Profile.Id == device.UserId).Profile, AccountWire.Secret(), AccountWire.Secret());
            Device replacement = device with
            {
                AccessHash = AccountWire.Hash(reply.AccessToken),
                RefreshHash = AccountWire.Hash(reply.RefreshToken),
                AccessUntil = now.AddMinutes(10),
                PreviousHash = device.RefreshHash,
                RetryId = requestId,
                RetryUntil = now.AddSeconds(30),
                RetryReply = reply,
                RetiredHashes = [.. device.RetiredHashes ?? [], device.RefreshHash]
            };
            Save(current with { Devices = current.Devices.Select(d => d == device ? replacement : d).ToArray() });
            return reply;
        }
    }

    public void Logout(string refresh)
    {
        AccountWire.Need(AccountWire.Token(refresh), "unauthorized", 401);
        lock (gate)
        {
            State current = Prune();
            Save(current with
            {
                Devices = current.Devices.Where(d => !AccountWire.Matches(refresh, d.RefreshHash)
                && !(d.PreviousHash is string previous && AccountWire.Matches(refresh, previous))).ToArray()
            });
        }
    }

    public void Sweep()
    {
        lock (gate)
        {
            State current = Prune();
            if (!current.Devices.SequenceEqual(state.Devices)) Save(current);
        }
    }
    private State Prune() => state with
    {
        Devices = state.Devices.Where(d => d.RefreshUntil > clock.GetUtcNow())
        .Select(d => d.RetryUntil <= clock.GetUtcNow() && d.RetryReply is not null ? d with { RetryReply = null, RetryId = null, RetryUntil = null } : d).ToArray()
    };
    private void Save(State replacement)
    {
        AccountWire.Need(replacement.Users.Length <= 10000 && replacement.Devices.Length <= 10000, "accountCapacity", 503);
        byte[] bytes = protector.Protect(JsonSerializer.SerializeToUtf8Bytes(replacement, AccountWire.Json));
        AccountWire.Need(bytes.Length <= MaximumBytes, "accountCapacity", 503);
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (FileStream output = new(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            { output.Write(bytes); output.Flush(flushToDisk: true); }
            File.Move(temporary, path, overwrite: true);
            state = replacement;
        }
        finally { File.Delete(temporary); }
    }
    public void Dispose() => lease.Dispose();
}
