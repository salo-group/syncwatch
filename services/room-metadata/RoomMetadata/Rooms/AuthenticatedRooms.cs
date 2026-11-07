using System.Text.Json;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Rooms;

public sealed record AuthenticatedAdmission(string RoomId, string MemberId, DateTimeOffset ExpiresAt, Bootstrap Bootstrap);
public sealed record RoomInvitation(string RoomId, string Invitation, int ExpiresIn)
{
    public override string ToString() => "[room invitation redacted]";
}
public sealed record AdmissionRequest(string Id, string Status, string Name, string AccountId, string Email);

/// <summary>
/// Account authority around a private ordered room store. Legacy membership credentials never
/// cross this boundary; every operation is bound to the active device session and client run.
/// </summary>
public sealed class AuthenticatedRooms
{
    private sealed record Member(AccountPrincipal Principal, string Name, Admission Admission, MemberBinding Binding, bool Owner);
    private sealed class Room(string id, string epoch, DateTimeOffset expiresAt)
    {
        internal readonly string Id = id, Epoch = epoch;
        internal readonly DateTimeOffset ExpiresAt = expiresAt;
        internal readonly Dictionary<string, Member> Members = new(StringComparer.Ordinal);
        internal byte[]? InviteHash;
        internal DateTimeOffset InviteUntil;
    }
    private sealed class Request(AccountPrincipal principal, AccountProfile profile, string roomId, string run, string digest, DateTimeOffset until)
    {
        internal readonly string Id = Wire.NewId("request-");
        internal readonly AccountPrincipal Principal = principal;
        internal readonly AccountProfile Profile = profile;
        internal readonly string RoomId = roomId, Run = run, Name = profile.Name, Digest = digest;
        internal readonly DateTimeOffset Until = until;
        internal string Status = "pending";
        internal string? MemberId;
    }
    private sealed record Creation(string RoomId, string Run, DateTimeOffset Until);
    private readonly object gate = new();
    private readonly AccountRepository accounts;
    private readonly RoomStore metadata;
    private readonly RoomLimits limits;
    private readonly TimeProvider clock;
    private readonly string host;
    private readonly int port;
    private readonly Dictionary<string, Room> rooms = new(StringComparer.Ordinal);
    private readonly Dictionary<(string Session, string Id), Request> requests = [];
    private readonly Dictionary<(string Session, string Id), Creation> creations = [];
    public SyncAdmissionTickets Tickets { get; }

    public AuthenticatedRooms(AccountRepository accounts, string host, int port, RoomLimits? limits = null, TimeProvider? clock = null)
    {
        Wire.Need(Wire.Text(host, 253) && Uri.CheckHostName(host) != UriHostNameType.Unknown && port is >= 1 and <= 65535);
        this.accounts = accounts; this.host = host; this.port = port;
        this.limits = limits ?? new(); this.clock = clock ?? TimeProvider.System;
        metadata = new(this.limits, this.clock);
        Tickets = new(IsAdmitted, this.clock);
    }

    public AuthenticatedAdmission Create(AccountPrincipal principal, string name, string run, string requestId)
    {
        Validate(principal, name, run, requestId);
        lock (gate)
        {
            SweepCore(); NeedActive(principal);
            var key = (principal.SessionId, requestId);
            if (creations.TryGetValue(key, out Creation? previous))
            {
                Wire.Need(previous.Run == run, "conflict", 409);
                Room existing = Find(previous.RoomId);
                return AdmissionFor(existing.Members.Values.Single(member => member.Owner && Same(member.Principal, principal)));
            }
            Wire.Need(creations.Count < 256 && rooms.Count < limits.MaximumRooms
                && rooms.Values.Count(room => room.Members.Values.Any(member => member.Owner && member.Principal.AccountId == principal.AccountId)) < 4, "queueFull", 429);
            Admission admission = metadata.Create(JsonSerializer.SerializeToElement(new
            {
                v = 1,
                clientRunId = run,
                syncplayUsername = Username(),
                syncplayHost = host,
                syncplayPort = port,
                syncplayRoom = "sw" + Guid.NewGuid().ToString("N")
            }));
            Room room = new(admission.RoomId, admission.Bootstrap.Snapshot.RoomEpoch, admission.ExpiresAt);
            Member owner = Bind(principal, name, admission, owner: true);
            room.Members.Add(owner.Binding.MemberId, owner); rooms.Add(room.Id, room);
            creations.Add(key, new(room.Id, run, room.ExpiresAt));
            return AdmissionFor(owner);
        }
    }

    public RoomInvitation Invite(AccountPrincipal principal, string roomId, string memberId, string run)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(principal, roomId, memberId, run);
            Wire.Need(member.Owner, "forbidden", 403);
            string secret = Wire.Secret(); room.InviteHash = Wire.Hash(secret);
            room.InviteUntil = clock.GetUtcNow() + limits.InvitationLifetime;
            return new(room.Id, secret, checked((int)limits.InvitationLifetime.TotalSeconds));
        }
    }

    public AdmissionRequest RequestAdmission(AccountPrincipal principal, string name, string roomId, string run, string requestId, string invitation)
    {
        Validate(principal, name, run, requestId);
        Wire.Need(invitation is { Length: 64 } && invitation.All(char.IsAsciiHexDigitUpper), "unauthorized", 401);
        string digest = Convert.ToHexString(Wire.Hash(roomId + "\n" + run + "\n" + invitation));
        lock (gate)
        {
            SweepCore(); NeedActive(principal);
            var key = (principal.SessionId, requestId);
            if (requests.TryGetValue(key, out Request? previous))
            {
                Wire.Need(previous.Digest == digest && Same(previous.Principal, principal), "conflict", 409);
                return View(previous);
            }
            Room room = Find(roomId);
            Wire.Need(room.InviteHash is not null && room.InviteUntil > clock.GetUtcNow()
                && Wire.SecretMatches(invitation, room.InviteHash), "unauthorized", 401);
            Wire.Need(requests.Count < 256 && requests.Values.Count(request => request.RoomId == roomId && request.Status is "pending" or "approved") < limits.MaximumMembers
                && requests.Values.Count(request => request.Principal.AccountId == principal.AccountId && request.Status is "pending" or "approved") < 8, "queueFull", 429);
            Wire.Need(!room.Members.Values.Any(member => Same(member.Principal, principal) && member.Binding.ClientRunId == run), "conflict", 409);
            Request request = new(principal, accounts.Profile(principal), roomId, run, digest, clock.GetUtcNow().AddMinutes(2));
            requests.Add(key, request); room.InviteHash = null;
            return View(request);
        }
    }

    public AdmissionRequest[] Pending(AccountPrincipal principal, string roomId, string memberId, string run)
    {
        lock (gate)
        {
            (_, Member owner) = Authorize(principal, roomId, memberId, run); Wire.Need(owner.Owner, "forbidden", 403);
            return requests.Where(item => item.Value.RoomId == roomId && item.Value.Status == "pending")
                .Select(item => View(item.Value)).ToArray();
        }
    }

    public void Decide(AccountPrincipal principal, string roomId, string memberId, string run, string requestId, bool approve)
    {
        lock (gate)
        {
            (_, Member owner) = Authorize(principal, roomId, memberId, run); Wire.Need(owner.Owner, "forbidden", 403);
            Request[] found = requests.Values.Where(request => request.Id == requestId && request.RoomId == roomId).ToArray();
            Wire.Need(found.Length == 1, "unavailable", 404);
            Request request = found[0]; string next = approve ? "approved" : "denied";
            if (request.Status == next) return;
            Wire.Need(request.Status == "pending", "conflict", 409); NeedActive(request.Principal);
            request.Status = next;
        }
    }

    public AdmissionRequest Status(AccountPrincipal principal, string requestId, string run)
    {
        lock (gate) { SweepCore(); return View(OwnRequest(principal, requestId, run)); }
    }

    public AuthenticatedAdmission Accept(AccountPrincipal principal, string requestId, string run)
    {
        lock (gate)
        {
            SweepCore(); Request request = OwnRequest(principal, requestId, run); Room room = Find(request.RoomId);
            if (request.Status == "admitted")
            {
                Wire.Need(room.Members.TryGetValue(request.MemberId!, out Member? existing), "unauthorized", 401);
                return AdmissionFor(existing!);
            }
            Wire.Need(request.Status == "approved", "admissionRequired", 403);
            Admission admission = metadata.Admit(room.Id, run, Username());
            Member member = Bind(principal, request.Name, admission, owner: false);
            room.Members.Add(member.Binding.MemberId, member);
            request.Status = "admitted"; request.MemberId = member.Binding.MemberId;
            return AdmissionFor(member);
        }
    }

    /// <summary>Withdraw by the original device/run correlation, including a lost accept response.</summary>
    public void CancelAdmission(AccountPrincipal principal, string requestId, string run)
    {
        lock (gate)
        {
            SweepCore(); Request request = OwnRequest(principal, requestId, run);
            if (request.Status == "admitted" && rooms.TryGetValue(request.RoomId, out Room? room)
                && room.Members.TryGetValue(request.MemberId!, out Member? member))
            {
                Wire.Need(!member.Owner && Same(member.Principal, principal) && member.Binding.ClientRunId == run, "unauthorized", 401);
                Remove(room, member);
            }
            else if (request.Status is "pending" or "approved") request.Status = "revoked";
        }
    }

    public RoomMemberAccess Access(AccountPrincipal principal, string roomId, string memberId, string run, bool owner = false)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(principal, roomId, memberId, run);
            Wire.Need(!owner || member.Owner, "forbidden", 403);
            return new(room.Id, room.Epoch, room.Members.Values.Single(item => item.Owner).Principal.AccountId,
                principal, member.Binding.MemberId, member.Binding.ClientRunId, member.Owner);
        }
    }
    public bool IsCurrent(RoomMemberAccess access)
    {
        lock (gate)
        {
            try { return Access(access.Principal, access.RoomId, access.MemberId, access.ClientRunId, access.Owner) == access; }
            catch (RoomException error) when (error.Status is 401 or 403 or 404) { return false; }
        }
    }
    public Bootstrap Snapshot(AccountPrincipal principal, string roomId, string memberId, string run)
    {
        lock (gate) { (_, Member member) = Authorize(principal, roomId, memberId, run); return metadata.Snapshot(roomId, member.Admission.Token); }
    }

    public CatalogPage Catalog(AccountPrincipal principal, string roomId, string memberId, string run, string cursor)
    {
        lock (gate) { (_, Member member) = Authorize(principal, roomId, memberId, run); return metadata.Catalog(roomId, member.Admission.Token, cursor); }
    }

    public SelectionDefinition Selection(AccountPrincipal principal, string roomId, string memberId, string run, long generation)
    {
        lock (gate) { (_, Member member) = Authorize(principal, roomId, memberId, run); return metadata.Selection(roomId, member.Admission.Token, generation); }
    }

    public CommandResult Execute(AccountPrincipal principal, string roomId, string memberId, string run, Command command)
    {
        lock (gate)
        {
            (_, Member member) = Authorize(principal, roomId, memberId, run);
            // Calls are a separate aggregate; legacy commands cannot publish arbitrary links.
            Wire.Need(command.Type != "call.linkChanged", "forbidden", 403);
            return metadata.Execute(roomId, member.Admission.Token, command);
        }
    }

    public RoomStream Attach(AccountPrincipal principal, string roomId, string memberId, string run, string? epoch, long? cursor)
    {
        lock (gate) { (_, Member member) = Authorize(principal, roomId, memberId, run); return metadata.Attach(roomId, member.Admission.Token, epoch, cursor); }
    }

    public void Detach(string roomId, RoomStream stream) { lock (gate) metadata.Detach(roomId, stream); }

    public SyncAdmissionTicket Synchronize(AccountPrincipal principal, string roomId, string memberId, string run)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(principal, roomId, memberId, run);
            return Tickets.Issue(new(principal, roomId, room.Epoch, member.Binding, member.Admission.Bootstrap.Connection));
        }
    }

    public void Leave(AccountPrincipal principal, string roomId, string memberId, string run, string? revokeMemberId = null)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(principal, roomId, memberId, run);
            Wire.Need(revokeMemberId is null || member.Owner, "forbidden", 403);
            Member target = member;
            if (revokeMemberId is not null)
            {
                Wire.Need(room.Members.TryGetValue(revokeMemberId, out target!), "unavailable", 404);
                Wire.Need(!target.Owner, "forbidden", 403);
            }
            Remove(room, target);
        }
    }

    public bool IsAdmitted(SyncAdmissionBinding binding)
    {
        lock (gate)
        {
            SweepCore();
            return rooms.TryGetValue(binding.RoomId, out Room? room) && room.Epoch == binding.RoomEpoch
                && room.Members.TryGetValue(binding.Member.MemberId, out Member? member) && member.Binding == binding.Member
                && Same(member.Principal, binding.Principal) && member.Admission.Bootstrap.Connection == binding.Destination;
        }
    }

    public void Sweep() { lock (gate) SweepCore(); }

    // Server-owned call cleanup uses room identity, never a departed device's credentials.
    internal bool HasCurrentOwner(string roomId, string epoch, string accountId)
    {
        lock (gate)
        {
            SweepCore();
            return rooms.TryGetValue(roomId, out Room? room) && room.Epoch == epoch
                && room.Members.Values.Any(member => member.Owner && member.Principal.AccountId == accountId);
        }
    }

    private (Room, Member) Authorize(AccountPrincipal principal, string roomId, string memberId, string run)
    {
        SweepCore(); NeedActive(principal); Room room = Find(roomId);
        Wire.Need(room.Members.TryGetValue(memberId, out Member? member) && Same(member.Principal, principal)
            && member.Binding.ClientRunId == run, "unauthorized", 401);
        return (room, member!);
    }
    private Request OwnRequest(AccountPrincipal principal, string requestId, string run)
    {
        NeedActive(principal);
        Wire.Need(requests.TryGetValue((principal.SessionId, requestId), out Request? request)
            && Same(request.Principal, principal) && request.Run == run, "unauthorized", 401);
        return request!;
    }
    private Room Find(string id)
    {
        Wire.Need(rooms.TryGetValue(id, out Room? room), "unavailable", 404); return room!;
    }
    private void Remove(Room room, Member member)
    {
        try { metadata.Leave(room.Id, member.Admission.Token); }
        catch (RoomException error) when (error.Status is 401 or 404) { }
        if (member.Owner)
        {
            rooms.Remove(room.Id); Tickets.RevokeRoom(room.Id);
            foreach (Request request in requests.Values.Where(request => request.RoomId == room.Id)) request.Status = "closed";
        }
        else
        {
            room.Members.Remove(member.Binding.MemberId); Tickets.RevokeMember(room.Id, room.Epoch, member.Binding.MemberId);
            foreach (Request request in requests.Values.Where(request => request.MemberId == member.Binding.MemberId)) request.Status = "revoked";
        }
    }
    private void SweepCore()
    {
        metadata.Sweep(); DateTimeOffset now = clock.GetUtcNow();
        foreach (Room room in rooms.Values.ToArray())
        {
            foreach (Member member in room.Members.Values.OrderByDescending(member => member.Owner).ToArray())
            {
                if (now >= room.ExpiresAt || !accounts.IsActive(member.Principal)
                    || !metadata.Contains(room.Id, room.Epoch, member.Binding, member.Admission.Token)) Remove(room, member);
                if (!rooms.ContainsKey(room.Id)) break;
            }
        }
        foreach (Request request in requests.Values)
        {
            if (request.Status is "pending" or "approved" && (request.Until <= now || !accounts.IsActive(request.Principal))) request.Status = "expired";
        }
        foreach (var key in requests.Where(item => item.Value.Until.AddMinutes(2) <= now).Select(item => item.Key).ToArray()) requests.Remove(key);
        foreach (var key in creations.Where(item => item.Value.Until <= now).Select(item => item.Key).ToArray()) creations.Remove(key);
    }
    private static Member Bind(AccountPrincipal principal, string name, Admission admission, bool owner) =>
        new(principal, name, admission, admission.Bootstrap.Snapshot.Members.Single(member => member.MemberId == admission.MemberId), owner);
    private AuthenticatedAdmission AdmissionFor(Member member) => new(member.Admission.RoomId, member.Binding.MemberId,
        member.Admission.ExpiresAt, metadata.Snapshot(member.Admission.RoomId, member.Admission.Token));
    private static AdmissionRequest View(Request request) => new(request.Id, request.Status, request.Name, request.Profile.Id, request.Profile.Email);
    private string Username()
    {
        string value;
        do { value = "u" + Guid.NewGuid().ToString("N")[..15]; }
        while (rooms.Values.Any(room => room.Members.Values.Any(member => member.Binding.SyncplayUsername == value)));
        return value;
    }
    private void NeedActive(AccountPrincipal principal) => Wire.Need(accounts.IsActive(principal), "unauthorized", 401);
    private static bool Same(AccountPrincipal left, AccountPrincipal right) => left.AccountId == right.AccountId && left.SessionId == right.SessionId;
    private static void Validate(AccountPrincipal principal, string name, string run, string requestId)
    {
        ArgumentNullException.ThrowIfNull(principal);
        Wire.Need(Wire.Text(name, 128) && Wire.Id(run) && Wire.Id(requestId));
    }
}
