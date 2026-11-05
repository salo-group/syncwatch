using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Rooms;

/// <summary>Server-owned scope. No field is accepted as an identity claim from a synchronization client.</summary>
public sealed record SyncAdmissionBinding(AccountPrincipal Principal, string RoomId, string RoomEpoch,
    MemberBinding Member, SyncplayBinding Destination)
{
    public override string ToString() => "[synchronization admission binding redacted]";
}

public sealed record SyncAdmissionTicket(string Ticket, int ExpiresIn)
{
    public override string ToString() => "[synchronization ticket redacted]";
}

/// <summary>Bounded, single-use tickets. The trusted admission host must enforce the returned scope.</summary>
public sealed class SyncAdmissionTickets
{
    private sealed record Pending(SyncAdmissionBinding Binding, DateTimeOffset ExpiresAt);
    private readonly object gate = new();
    private readonly Dictionary<string, Pending> pending = new(StringComparer.Ordinal);
    private readonly Func<SyncAdmissionBinding, bool> authorized;
    private readonly TimeProvider clock;
    private readonly int capacity;
    private readonly int lifetimeSeconds;

    public SyncAdmissionTickets(Func<SyncAdmissionBinding, bool> authorized, TimeProvider? clock = null,
        int capacity = 256, int lifetimeSeconds = 30)
    {
        ArgumentNullException.ThrowIfNull(authorized);
        if (capacity is < 1 or > 4096 || lifetimeSeconds is < 1 or > 60) throw new ArgumentOutOfRangeException(nameof(capacity));
        this.authorized = authorized; this.clock = clock ?? TimeProvider.System;
        this.capacity = capacity; this.lifetimeSeconds = lifetimeSeconds;
    }

    public SyncAdmissionTicket Issue(SyncAdmissionBinding binding)
    {
        Validate(binding);
        Wire.Need(authorized(binding), "unauthorized", 401);
        lock (gate)
        {
            Sweep();
            // A replacement invalidates an unused ticket for the same member/run.
            Remove(value => value.RoomId == binding.RoomId && value.RoomEpoch == binding.RoomEpoch
                && value.Member.MemberId == binding.Member.MemberId);
            Wire.Need(pending.Count < capacity, "queueFull", 429);
            string ticket = Wire.Secret();
            pending.Add(Key(ticket), new(binding, clock.GetUtcNow().AddSeconds(lifetimeSeconds)));
            return new(ticket, lifetimeSeconds);
        }
    }

    public SyncAdmissionBinding Consume(string ticket, string clientRunId, string username, SyncplayBinding listener)
    {
        Wire.Need(ticket is { Length: 64 } && ticket.All(char.IsAsciiHexDigitUpper), "unauthorized", 401);
        Pending? grant;
        lock (gate)
        {
            Sweep();
            Wire.Need(pending.Remove(Key(ticket), out grant), "unauthorized", 401);
        }
        // Invalid scope also consumes the ticket; it cannot be probed until a combination matches.
        SyncAdmissionBinding binding = grant!.Binding;
        Wire.Need(binding.Member.ClientRunId == clientRunId && binding.Member.SyncplayUsername == username
            && binding.Destination == listener && authorized(binding) && grant.ExpiresAt > clock.GetUtcNow(), "unauthorized", 401);
        return binding;
    }

    public void RevokeRoom(string roomId) { lock (gate) Remove(value => value.RoomId == roomId); }
    public void RevokeMember(string roomId, string roomEpoch, string memberId)
    {
        lock (gate) Remove(value => value.RoomId == roomId && value.RoomEpoch == roomEpoch && value.Member.MemberId == memberId);
    }
    private static string Key(string ticket) => Convert.ToHexString(Wire.Hash(ticket));
    private void Sweep()
    {
        DateTimeOffset now = clock.GetUtcNow();
        foreach (string key in pending.Where(item => item.Value.ExpiresAt <= now).Select(item => item.Key).ToArray()) pending.Remove(key);
    }
    private void Remove(Func<SyncAdmissionBinding, bool> predicate)
    {
        foreach (string key in pending.Where(item => predicate(item.Value.Binding)).Select(item => item.Key).ToArray()) pending.Remove(key);
    }
    private static void Validate(SyncAdmissionBinding binding)
    {
        Wire.Need(Wire.Id(binding.RoomId) && Wire.Id(binding.RoomEpoch) && Wire.Id(binding.Member.MemberId)
            && Wire.Id(binding.Member.ClientRunId) && Wire.Text(binding.Member.SyncplayUsername, 64)
            && Wire.Text(binding.Destination.Room, 64) && Wire.Text(binding.Destination.Host, 253)
            && Uri.CheckHostName(binding.Destination.Host) != UriHostNameType.Unknown
            && binding.Destination.Port is >= 1 and <= 65535 && binding.Destination.RequireTls);
    }
}
