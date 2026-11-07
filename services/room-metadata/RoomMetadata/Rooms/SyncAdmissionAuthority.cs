namespace SyncWatch.RoomMetadata.Rooms;

public sealed record SyncAuthorityLease(string Id, string MemberId, string RoomEpoch, string ClientRunId,
    string Username, string Room, int ExpiresIn = 5)
{
    public override string ToString() => "[synchronization authority lease redacted]";
}

public sealed record SyncAuthorityUpdate(string Id, SyncAuthorityLease? Lease);

/// <summary>Authority for one owned server process. No account credential crosses its private pipe.</summary>
public sealed class SyncAdmissionAuthority
{
    private sealed record Active(SyncAdmissionBinding Binding, SyncAuthorityLease Lease);
    private readonly object gate = new();
    private readonly AuthenticatedRooms rooms;
    private readonly string host;
    private readonly int port;
    private readonly Dictionary<string, Active> active = new(StringComparer.Ordinal);

    public SyncAdmissionAuthority(AuthenticatedRooms rooms, string host, int port)
    {
        Wire.Need(Wire.Text(host, 253) && Uri.CheckHostName(host) != UriHostNameType.Unknown && port is >= 1 and <= 65535);
        this.rooms = rooms; this.host = host; this.port = port;
    }

    public SyncAuthorityLease Consume(string ticket, string run, string username, string room)
    {
        lock (gate)
        {
            Wire.Need(active.Count < 256, "queueFull", 429);
            SyncAdmissionBinding binding = rooms.Tickets.Consume(ticket, run, username, new(host, port, room));
            SyncAuthorityLease lease = new(Wire.NewId("lease-"), binding.Member.MemberId, binding.RoomEpoch,
                binding.Member.ClientRunId, binding.Member.SyncplayUsername, binding.Destination.Room);
            active.Add(lease.Id, new(binding, lease));
            return lease;
        }
    }

    public SyncAuthorityUpdate[] Updates()
    {
        lock (gate)
        {
            List<SyncAuthorityUpdate> updates = [];
            foreach ((string id, Active value) in active.ToArray())
            {
                bool admitted = rooms.IsAdmitted(value.Binding);
                updates.Add(new(id, admitted ? value.Lease : null));
                if (!admitted) active.Remove(id);
            }
            return [.. updates];
        }
    }

    // A close applies to the exact transport lease, never its replacement.
    public void Release(string leaseId) { lock (gate) active.Remove(leaseId); }
    public void Clear() { lock (gate) active.Clear(); }

}
