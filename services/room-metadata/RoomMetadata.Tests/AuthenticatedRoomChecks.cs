using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

internal static class AuthenticatedRoomChecks
{
    internal static void Run()
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-authenticated-room-" + Guid.NewGuid().ToString("N"));
        var clock = new Clock();
        try
        {
            using var accounts = new AccountRepository(directory, new EphemeralDataProtectionProvider(), clock);
            DeviceSession ownerSession = accounts.SignIn("owner", "Owner", "owner@example.invalid");
            DeviceSession guestSession = accounts.SignIn("guest", "Guest", "guest@example.invalid");
            DeviceSession siblingSession = accounts.SignIn("owner", "Other device", "owner@example.invalid");
            AccountPrincipal owner = accounts.Authorize(ownerSession.AccessToken), guest = accounts.Authorize(guestSession.AccessToken), sibling = accounts.Authorize(siblingSession.AccessToken);
            var limits = new RoomLimits { MaximumRooms = 4, MaximumMembers = 3, RoomLifetime = TimeSpan.FromHours(1) };
            var rooms = new AuthenticatedRooms(accounts, "syncplay.example.invalid", 8999, limits, clock);
            AuthenticatedAdmission created = rooms.Create(owner, "Owner", "owner-run", "create-1");
            string room = created.RoomId, ownerMember = created.MemberId;
            Require(rooms.Create(owner, "Owner", "owner-run", "create-1").MemberId == ownerMember);
            Reject(() => rooms.Create(owner, "Owner", "changed-run", "create-1"), "conflict", 409);
            Require(created.Bootstrap.Connection.Host == "syncplay.example.invalid" && created.Bootstrap.Connection.RequireTls
                && created.Bootstrap.Connection.Room.Length <= 35 && created.Bootstrap.Snapshot.Members.Single().SyncplayUsername.Length <= 16);
            string publicReply = JsonSerializer.Serialize(created, Wire.Json);
            Require(!publicReply.Contains("token", StringComparison.OrdinalIgnoreCase) && !publicReply.Contains("invitation", StringComparison.OrdinalIgnoreCase));
            using RoomStream ownerStream = rooms.Attach(owner, room, ownerMember, "owner-run", null, null);
            Reject(() => rooms.Snapshot(sibling, room, ownerMember, "owner-run"));
            Reject(() => rooms.Snapshot(owner, room, ownerMember, "foreign-run"));
            RoomInvitation superseded = rooms.Invite(owner, room, ownerMember, "owner-run");
            RoomInvitation invitation = rooms.Invite(owner, room, ownerMember, "owner-run");
            Require(!invitation.ToString().Contains(invitation.Invitation, StringComparison.Ordinal));
            Reject(() => rooms.RequestAdmission(guest, "Guest", room, "guest-run", "request-1", superseded.Invitation));
            AdmissionRequest requested = rooms.RequestAdmission(guest, "Guest", room, "guest-run", "request-1", invitation.Invitation);
            Require(requested.Status == "pending" && requested.Id != "request-1");
            Require(rooms.RequestAdmission(guest, "Guest", room, "guest-run", "request-1", invitation.Invitation).Id == requested.Id);
            Reject(() => rooms.RequestAdmission(guest, "Guest", room, "other-run", "request-1", invitation.Invitation), "conflict", 409);
            Reject(() => rooms.RequestAdmission(sibling, "Sibling", room, "sibling-run", "request-1", invitation.Invitation));
            Reject(() => rooms.Accept(guest, "request-1", "guest-run"), "admissionRequired", 403);
            Reject(() => rooms.Status(sibling, "request-1", "guest-run"));
            Reject(() => rooms.Pending(sibling, room, ownerMember, "owner-run"));
            Require(rooms.Pending(owner, room, ownerMember, "owner-run").Single().Id == requested.Id);
            rooms.Decide(owner, room, ownerMember, "owner-run", requested.Id, true);
            rooms.Decide(owner, room, ownerMember, "owner-run", requested.Id, true);
            AuthenticatedAdmission admitted = rooms.Accept(guest, "request-1", "guest-run");
            Require(rooms.Accept(guest, "request-1", "guest-run").MemberId == admitted.MemberId);
            string guestMember = admitted.MemberId;
            using RoomStream guestStream = rooms.Attach(guest, room, guestMember, "guest-run", null, null);
            Require(rooms.Snapshot(guest, room, guestMember, "guest-run").Snapshot.Members.Length == 2);
            Reject(() => rooms.Invite(guest, room, guestMember, "guest-run"), "forbidden", 403);
            Reject(() => rooms.Leave(guest, room, guestMember, "guest-run", ownerMember), "forbidden", 403);

            SyncAdmissionTicket ticket = rooms.Synchronize(guest, room, guestMember, "guest-run");
            MemberBinding binding = admitted.Bootstrap.Snapshot.Members.Single(member => member.MemberId == guestMember);
            SyncAdmissionBinding scope = rooms.Tickets.Consume(ticket.Ticket, "guest-run", binding.SyncplayUsername, admitted.Bootstrap.Connection);
            Require(rooms.IsAdmitted(scope));
            Reject(() => rooms.Tickets.Consume(ticket.Ticket, "guest-run", binding.SyncplayUsername, admitted.Bootstrap.Connection));
            Bootstrap snapshot = rooms.Snapshot(guest, room, guestMember, "guest-run");
            _ = rooms.Synchronize(guest, room, guestMember, "guest-run");
            Require(rooms.Catalog(guest, room, guestMember, "guest-run", snapshot.CatalogCursor).Items.Length == 0);
            Command prohibited = Wire.ReadCommand(JsonSerializer.SerializeToUtf8Bytes(new
            {
                v = 1, id = "cmd-1", type = "call.linkChanged", clientRunId = "guest-run", roomEpoch = snapshot.Snapshot.RoomEpoch,
                watchGeneration = 0, payload = new { meetUri = "https://meet.google.com/aaa-bbbb-ccc" }
            }));
            Reject(() => rooms.Execute(guest, room, guestMember, "guest-run", prohibited), "forbidden", 403);
            rooms.Leave(owner, room, ownerMember, "owner-run", guestMember);
            Require(guestStream.Stopped.IsCancellationRequested && !rooms.IsAdmitted(scope));
            Reject(() => rooms.Snapshot(guest, room, guestMember, "guest-run"));
            Reject(() => rooms.Accept(guest, "request-1", "guest-run"), "admissionRequired", 403);

            // Two devices may choose the same retry ID. Approval uses a server handle.
            RoomInvitation first = rooms.Invite(owner, room, ownerMember, "owner-run");
            AdmissionRequest guestAgain = rooms.RequestAdmission(guest, "Guest", room, "guest-run-2", "same-id", first.Invitation);
            RoomInvitation second = rooms.Invite(owner, room, ownerMember, "owner-run");
            AdmissionRequest sameAccount = rooms.RequestAdmission(sibling, "Sibling", room, "sibling-run", "same-id", second.Invitation);
            Require(guestAgain.Id != sameAccount.Id);
            rooms.Decide(owner, room, ownerMember, "owner-run", sameAccount.Id, false);
            Require(rooms.Status(sibling, "same-id", "sibling-run").Status == "denied");
            Reject(() => rooms.Accept(sibling, "same-id", "sibling-run"), "admissionRequired", 403);
            rooms.Decide(owner, room, ownerMember, "owner-run", guestAgain.Id, true);
            AuthenticatedAdmission again = rooms.Accept(guest, "same-id", "guest-run-2");
            using RoomStream againStream = rooms.Attach(guest, room, again.MemberId, "guest-run-2", null, null);
            SyncAdmissionTicket beforeLogout = rooms.Synchronize(guest, room, again.MemberId, "guest-run-2");
            accounts.Logout(guestSession.RefreshToken); rooms.Sweep();
            Require(againStream.Stopped.IsCancellationRequested && !ownerStream.Stopped.IsCancellationRequested);
            Reject(() => rooms.Tickets.Consume(beforeLogout.Ticket, "guest-run-2", again.Bootstrap.Snapshot.Members.Single(member => member.MemberId == again.MemberId).SyncplayUsername, again.Bootstrap.Connection));

            // Expired invites and requests do not grant admission. Rotation retains membership.
            RoomInvitation expiring = rooms.Invite(owner, room, ownerMember, "owner-run");
            clock.Now = clock.Now.AddMinutes(2);
            Require(rooms.Status(sibling, "same-id", "sibling-run").Status == "denied");
            DeviceSession rotated = accounts.Refresh(ownerSession.RefreshToken, new string('r', 43));
            Require(rooms.Snapshot(accounts.Authorize(rotated.AccessToken), room, ownerMember, "owner-run").Snapshot.Members.Length == 1);
            RoomInvitation shortInvite = rooms.Invite(owner, room, ownerMember, "owner-run");
            AdmissionRequest pending = rooms.RequestAdmission(sibling, "Sibling", room, "other-run", "expires", shortInvite.Invitation);
            clock.Now = clock.Now.AddMinutes(2); rooms.Sweep();
            Require(rooms.Status(sibling, "expires", "other-run").Status == "expired");
            Reject(() => rooms.Decide(owner, room, ownerMember, "owner-run", pending.Id, true), "conflict", 409);
            accounts.Logout(rotated.RefreshToken); rooms.Sweep();
            Require(ownerStream.Stopped.IsCancellationRequested);
            Reject(() => rooms.Snapshot(sibling, room, ownerMember, "owner-run"), "unavailable", 404);
            Console.WriteLine("Authenticated room checks passed: device/run binding, one-use invites, approval, retry/collision, catalog stability and revocation");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true); }
    }

    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Authenticated room assertion failed"); }
    private static void Reject(Action action, string code = "unauthorized", int status = 401)
    {
        try { action(); } catch (RoomException error) when (error.Code == code && error.Status == status) { return; }
        throw new InvalidOperationException("Authenticated room rejection missing");
    }
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
}
