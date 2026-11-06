using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;
using SyncWatch.RoomMetadata.Rooms;

internal static class MeetAuthorizationChecks
{
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
    internal static void Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet authorization requires Windows validation");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-authorization-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var clock = new Clock();
            var protection = DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")), builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Authorization.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
            using var accounts = new AccountRepository(root, protection, clock);
            using var grants = new MeetGrantRepository(accounts, root, protection);
            var owner = accounts.SignIn("authorization-owner", "Owner", "owner@example.invalid");
            var guest = accounts.SignIn("authorization-guest", "Guest", "guest@example.invalid");
            var principal = accounts.Authorize(owner.AccessToken); var other = accounts.Authorize(guest.AccessToken);
            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999, clock: clock);
            var admission = rooms.Create(principal, "Owner", "auth-owner-run", "auth-room-create");
            var scope = rooms.Access(principal, admission.RoomId, admission.MemberId, "auth-owner-run", owner: true);
            var transactions = new MeetAuthorizationTransactions(accounts, rooms, grants, new Uri("http://127.0.0.1:8788"), clock);
            string proof = Secret(), challenge = Hash(proof);
            MeetAuthorizationStart Start() => transactions.Start(scope, challenge);
            var cancelled = Start();
            Require(cancelled.BrowserUri.StartsWith("http://127.0.0.1:8788/auth/google/meet-start?id=", StringComparison.Ordinal));
            Require(!cancelled.BrowserUri.Contains(proof, StringComparison.Ordinal) && cancelled.ExpiresIn == 300 && cancelled.PollAfterMs == 2000);
            Reject(() => transactions.Poll(other, cancelled.TransactionId, proof), "unauthorized");
            Reject(() => transactions.Poll(principal, cancelled.TransactionId, Secret()), "unauthorized");
            Require(transactions.Poll(accounts.Authorize(owner.AccessToken), cancelled.TransactionId, proof).Account is null);
            Require(transactions.Cancel(principal, cancelled.TransactionId, proof).Status == "cancelled");
            Require(!grants.HasAuthorization(principal));
            var mismatch = Start(); transactions.BeginBrowser(mismatch.TransactionId);
            Require(transactions.CompleteBrowser(mismatch.TransactionId, "authorization-guest", "synthetic-refresh", true) is null);
            var rejected = transactions.Poll(principal, mismatch.TransactionId, proof);
            Require(rejected.Status == "failed" && rejected.Reason == "accountMismatch" && rejected.Account is null && !grants.HasAuthorization(principal));
            foreach (bool scopeGranted in new[] { false, true })
            {
                var unavailable = Start(); transactions.BeginBrowser(unavailable.TransactionId);
                Require(transactions.CompleteBrowser(unavailable.TransactionId, "authorization-owner", scopeGranted ? null : "synthetic-refresh", scopeGranted) is null);
                Require(transactions.Poll(principal, unavailable.TransactionId, proof).Reason == "permissionUnavailable");
            }
            var accepted = Start(); transactions.BeginBrowser(accepted.TransactionId);
            string code = transactions.CompleteBrowser(accepted.TransactionId, "authorization-owner", "owned-synthetic-meet-refresh", true, managedDomain: false)!;
            Require(code.Length == 12 && transactions.Poll(principal, accepted.TransactionId, proof).Account is null);
            var incorrect = transactions.Verify(principal, accepted.TransactionId, proof, "invalid");
            Require(incorrect.Status == "browserConfirmationRequired" && incorrect.Reason == "invalidConfirmation" && incorrect.Account is null);
            var verified = transactions.Verify(principal, accepted.TransactionId, proof, code);
            Require(verified.Status == "confirmAuthorization" && verified.Account == owner.Account && !grants.HasAuthorization(principal));
            string confirmation = Secret();
            Require(transactions.Confirm(principal, accepted.TransactionId, proof, confirmation, code).Status == "complete");
            Require(grants.HasAuthorization(principal));
            Require(grants.ReadForProvider(principal.AccountId)?.ManagedDomain == false);
            Require(transactions.Confirm(principal, accepted.TransactionId, proof, confirmation, code).Status == "complete");
            Reject(() => transactions.Confirm(principal, accepted.TransactionId, proof, Secret(), code), "conflict");
            Require(transactions.Cancel(principal, accepted.TransactionId, proof).Status == "complete" && grants.HasAuthorization(principal));
            var reuse = Start(); transactions.BeginBrowser(reuse.TransactionId);
            Require(transactions.CompleteBrowser(reuse.TransactionId, "authorization-owner", null, true) is not null);
            Require(transactions.Cancel(principal, reuse.TransactionId, proof).Status == "cancelled" && grants.HasAuthorization(principal));

            var invitation = rooms.Invite(principal, admission.RoomId, admission.MemberId, "auth-owner-run");
            var request = rooms.RequestAdmission(other, "Guest", admission.RoomId, "auth-guest-run", "auth-guest-request", invitation.Invitation);
            rooms.Decide(principal, admission.RoomId, admission.MemberId, "auth-owner-run", request.Id, true);
            var joined = rooms.Accept(other, "auth-guest-request", "auth-guest-run");
            var guestScope = rooms.Access(other, admission.RoomId, joined.MemberId, "auth-guest-run");
            Reject(() => transactions.Start(guestScope, challenge), "forbidden");
            Reject(() => transactions.Start(guestScope with { Owner = true }, challenge), "forbidden");
            var stale = Start(); transactions.BeginBrowser(stale.TransactionId);
            rooms.Leave(principal, admission.RoomId, admission.MemberId, "auth-owner-run");
            Require(transactions.CompleteBrowser(stale.TransactionId, "authorization-owner", "must-not-save", true) is null);
            Require(transactions.Cancel(principal, stale.TransactionId, proof).Status == "cancelled");
            var next = rooms.Create(principal, "Owner", "auth-next-run", "auth-next-create");
            scope = rooms.Access(principal, next.RoomId, next.MemberId, "auth-next-run", owner: true);
            var expires = Start(); transactions.BeginBrowser(expires.TransactionId);
            clock.Now += TimeSpan.FromSeconds(301);
            Require(transactions.Poll(principal, expires.TransactionId, proof).Status == "expired");
            transactions.Sweep();
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_AUTHORIZATION_OK native/browser counterproof, matching subject, explicit confirmation, replay, guest denial, cancellation and expiry");
    }
    private static string Secret() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    private static string Hash(string value) => Convert.ToBase64String(SHA256.HashData(Encoding.ASCII.GetBytes(value))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet authorization invariant"); }
    private static void Reject(Action action, string code)
    {
        try { action(); throw new InvalidOperationException("Expected " + code); }
        catch (AccountException error) when (error.Code == code) { }
    }
}
