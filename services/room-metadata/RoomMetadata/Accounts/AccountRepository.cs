using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;

namespace SyncWatch.RoomMetadata.Accounts;

/// <summary>Bounded, single-writer encrypted store. Mutations become visible after flushed replacement.</summary>
public sealed class AccountRepository : IDisposable
{
    private sealed record User(string Issuer, string Subject, AccountProfile Profile);
    private sealed record Device(string UserId, string AccessHash, string RefreshHash, DateTimeOffset AccessUntil,
        DateTimeOffset RefreshUntil, string? PreviousHash = null, string? RetryId = null,
        DateTimeOffset? RetryUntil = null, DeviceSession? RetryReply = null, string[]? RetiredHashes = null);
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
        AccountWire.Need(AccountWire.Token(access), "unauthorized", 401);
        lock (gate)
        {
            Device? device = state.Devices.FirstOrDefault(d => AccountWire.Matches(access, d.AccessHash) && d.AccessUntil > clock.GetUtcNow());
            AccountWire.Need(device is not null, "unauthorized", 401);
            return state.Users.Single(u => u.Profile.Id == device!.UserId).Profile;
        }
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
            Device replacement = new(device.UserId, AccountWire.Hash(reply.AccessToken), AccountWire.Hash(reply.RefreshToken), now.AddMinutes(10),
                device.RefreshUntil, device.RefreshHash, requestId, now.AddSeconds(30), reply, [.. device.RetiredHashes ?? [], device.RefreshHash]);
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
                && !(d.PreviousHash is string previous && AccountWire.Matches(refresh, previous))
                && !(d.RetiredHashes ?? []).Any(hash => AccountWire.Matches(refresh, hash))).ToArray()
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
