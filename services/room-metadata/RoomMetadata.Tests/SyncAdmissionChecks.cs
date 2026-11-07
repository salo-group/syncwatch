using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

internal static class SyncAdmissionChecks
{
    internal static void Run()
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-sync-admission-" + Guid.NewGuid().ToString("N"));
        var clock = new Clock();
        try
        {
            using var accounts = new AccountRepository(directory, new EphemeralDataProtectionProvider(), clock);
            DeviceSession session = accounts.SignIn("subject", "Member", "member@example.invalid");
            DeviceSession sibling = accounts.SignIn("subject", "Sibling", "member@example.invalid");
            AccountPrincipal principal = accounts.Authorize(session.AccessToken);
            SyncAdmissionBinding binding = new(principal, "room-a", "epoch-a", new("member-a", "run-a", "member-a"), new("syncplay.example.invalid", 8999, "room-a"));
            bool admitted = true, slow = false;
            bool Authorized(SyncAdmissionBinding value)
            {
                if (slow) clock.Now = clock.Now.AddSeconds(31);
                return admitted && value.Principal.SessionId == principal.SessionId && value.RoomEpoch == "epoch-a" && accounts.IsActive(value.Principal);
            }
            var tickets = new SyncAdmissionTickets(Authorized, clock, capacity: 2);
            SyncAdmissionBinding Consume(string value, string run = "run-a", string username = "member-a", SyncplayBinding? listener = null) =>
                tickets.Consume(value, run, username, listener ?? binding.Destination);
            SyncAdmissionTicket Issue() => tickets.Issue(binding);
            SyncAdmissionTicket first = Issue();
            Require(first.ExpiresIn == 30 && !first.ToString().Contains(first.Ticket, StringComparison.Ordinal));
            Require(Consume(first.Ticket) == binding);
            Reject(() => Consume(first.Ticket));
            foreach (Action<string> wrong in new Action<string>[]
            {
                value => Consume(value, run: "other-run"),
                value => Consume(value, username: "other-user"),
                value => Consume(value, listener: binding.Destination with { Room = "other-room" }),
                value => Consume(value, listener: binding.Destination with { Host = "other.example.invalid" }),
                value => Consume(value, listener: binding.Destination with { Port = 9000 }),
                value => Consume(value, listener: binding.Destination with { RequireTls = false })
            })
            {
                SyncAdmissionTicket value = Issue(); Reject(() => wrong(value.Ticket)); Reject(() => Consume(value.Ticket));
            }
            Reject(() => Consume("not-a-ticket"));
            SyncAdmissionTicket replaced = Issue(), latest = Issue();
            Reject(() => Consume(replaced.Ticket)); Require(Consume(latest.Ticket) == binding);
            SyncAdmissionTicket expired = Issue(); clock.Now = clock.Now.AddSeconds(30);
            Reject(() => Consume(expired.Ticket));
            SyncAdmissionTicket duringCheck = Issue(); slow = true;
            Reject(() => Consume(duringCheck.Ticket)); slow = false;
            SyncAdmissionTicket revoked = Issue(); admitted = false;
            Reject(() => Consume(revoked.Ticket)); Reject(() => Issue()); admitted = true;
            Reject(() => tickets.Issue(binding with { Principal = accounts.Authorize(sibling.AccessToken) }));
            Reject(() => tickets.Issue(binding with { RoomEpoch = "epoch-other" }));
            SyncAdmissionTicket removed = Issue(); tickets.RevokeMember("room-a", "epoch-a", "member-a"); Reject(() => Consume(removed.Ticket));
            SyncAdmissionBinding other = binding with { RoomId = "room-b", Member = new("member-b", "run-b", "member-b"), Destination = binding.Destination with { Room = "room-b" } };
            SyncAdmissionTicket kept = tickets.Issue(other); removed = Issue();
            tickets.RevokeRoom("room-a"); Reject(() => Consume(removed.Ticket));
            Require(tickets.Consume(kept.Ticket, "run-b", "member-b", other.Destination) == other);
            _ = Issue(); _ = tickets.Issue(other);
            Reject(() => tickets.Issue(other with { Member = new("member-c", "run-c", "member-c") }), "queueFull", 429);
            clock.Now = clock.Now.AddSeconds(31); SyncAdmissionTicket concurrent = Issue();
            int successes = 0;
            Parallel.For(0, 8, _ =>
            {
                try { Consume(concurrent.Ticket); Interlocked.Increment(ref successes); }
                catch (RoomException error) when (error.Code == "unauthorized") { }
            });
            Require(successes == 1);
            SyncAdmissionTicket oldProcess = Issue();
            var restarted = new SyncAdmissionTickets(Authorized, clock);
            Reject(() => restarted.Consume(oldProcess.Ticket, "run-a", "member-a", binding.Destination));
            accounts.Logout(session.RefreshToken);
            Reject(() => Consume(oldProcess.Ticket)); Reject(() => Issue());
            Console.WriteLine("Sync admission ticket checks passed: scope/TLS binding, one use, replacement, expiry, revocation, capacity and concurrent replay");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true); }
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Sync admission assertion failed"); }
    private static void Reject(Action action, string code = "unauthorized", int status = 401)
    {
        try { action(); } catch (RoomException error) when (error.Code == code && error.Status == status) { return; }
        throw new InvalidOperationException("Synchronization admission rejection missing");
    }
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
}
