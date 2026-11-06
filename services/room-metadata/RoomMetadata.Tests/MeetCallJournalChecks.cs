using System.Text;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;
using SyncWatch.RoomMetadata.Rooms;

internal static class MeetCallJournalChecks
{
    internal static void Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet operation protection requires Windows validation");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-journal-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var protection = DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")), builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Journal.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
            using var accounts = new AccountRepository(root, protection);
            var device = accounts.SignIn("journal-owner", "Owner", "owner@example.invalid");
            var principal = accounts.Authorize(device.AccessToken);
            var scope = new RoomMemberAccess("room-owned", "epoch-owned", principal.AccountId, principal, "member-owned", "run-owned", true);
            string request = AccountWire.Secret(), file = Path.Combine(root, "meet", "calls.protected");
            MeetCallEntry interrupted;
            using (var journal = new MeetCallJournal(root, protection))
            {
                Reject(() => journal.Begin(scope with { Owner = false }, request), "forbidden");
                var (call, dispatch) = journal.Begin(scope, request); interrupted = call;
                Require(dispatch && call.Generation == 1 && journal.Latest(scope) == call);
                Require(journal.Begin(scope, request) == (call, false));
                Reject(() => journal.Begin(scope with { RoomEpoch = "other-epoch" }, request), "conflict");
                Reject(() => journal.Begin(scope, AccountWire.Secret()), "conflict");
                Require(!journal.Update(call with { State = "createFailed", Reason = "providerUnavailable" }, call with { State = "createUnknown", Reason = "providerOutcomeUnknown" }));
            }
            using (var restored = new MeetCallJournal(root, protection))
            {
                var call = restored.Latest(scope)!;
                Require(call.State == "createUnknown" && call.Reason == "providerOutcomeUnknown");
                Require(restored.Begin(scope, request) == (call, false));
                Reject(() => restored.Begin(scope, AccountWire.Secret()), "conflict");
                Require(!restored.Update(interrupted, interrupted with { State = "createFailed", Reason = "providerUnavailable" }));
            }
            var nextScope = scope with { RoomEpoch = "next-epoch" };
            string nextRequest = AccountWire.Secret();
            using (var journal = new MeetCallJournal(root, protection))
            {
                var (creating, dispatch) = journal.Begin(nextScope, nextRequest); Require(dispatch);
                Reject(() => journal.Update(creating, creating with { State = "ready", Space = "spaces/owned-space", MeetUri = "https://meet.google.com/abc-defg-hij" }), "conflict");
                var created = creating with { State = "created", Space = "spaces/owned-space", MeetUri = "https://meet.google.com/abc-defg-hij" };
                Require(journal.Update(creating, created));
                Require(File.ReadAllBytes(file).AsSpan().IndexOf(Encoding.UTF8.GetBytes(created.MeetUri!)) < 0);
                var ready = created with { State = "ready" }; Require(journal.Update(created, ready));
                Require(journal.ClosureCandidates().Any(candidate => candidate.Id == ready.Id));
                var checking = ready with { State = "checkingEnd", EndRequestId = AccountWire.Secret(), ClosureAttempted = true };
                Require(journal.Update(ready, checking));
                var ending = checking with { State = "ending", Conference = "conferenceRecords/owned-conference" };
                Require(journal.Update(checking, ending));
                Require(!journal.Update(ready, ready with { State = "ended" }));
            }
            using (var restored = new MeetCallJournal(root, protection))
            {
                var call = restored.Latest(nextScope)!; Require(call.State == "endUnknown" && call.Conference == "conferenceRecords/owned-conference");
                Require(call.ClosureAttempted && !restored.ClosureCandidates().Any(candidate => candidate.Id == call.Id));
                Reject(() => restored.Update(call, call with { ClosureAttempted = false }), "malformed");
                Reject(() => restored.Update(call, call with { State = "ending", Reason = null }), "conflict");
                Require(restored.Update(call, call with { State = "ended", Reason = null }));
                var (next, dispatch) = restored.Begin(nextScope, AccountWire.Secret()); Require(dispatch && next.Generation == 2);
                File.Move(file, file + ".backup"); Directory.CreateDirectory(file);
                try
                {
                    try { restored.Update(next, next with { State = "createFailed", Reason = "providerUnavailable" }); throw new InvalidOperationException("Expected atomic storage failure"); }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
                    Require(restored.Latest(nextScope) == next);
                }
                finally { Directory.Delete(file); File.Move(file + ".backup", file); }
            }
            Require(!Directory.EnumerateFiles(Path.GetDirectoryName(file)!, "*.tmp").Any());
            var deniedScope = scope with { RoomEpoch = "permission-epoch" };
            string rejectedVersion = AccountWire.Secret();
            using (var journal = new MeetCallJournal(root, protection))
            {
                var (call, dispatch) = journal.Begin(deniedScope, AccountWire.Secret()); Require(dispatch);
                Require(journal.Update(call, call with { State = "createFailed", Reason = "permissionRequired", RejectedGrantVersion = rejectedVersion }));
            }
            using (var restored = new MeetCallJournal(root, protection)) Require(restored.Latest(deniedScope)?.RejectedGrantVersion == rejectedVersion);
            try { protection.CreateProtector("SyncWatch.Meet", "grants", "v1").Unprotect(File.ReadAllBytes(file)); throw new InvalidOperationException("Journal purpose was shared"); }
            catch (System.Security.Cryptography.CryptographicException) { }
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_CALL_JOURNAL_OK write-before-dispatch, restart ambiguity, replay/conflict, conference binding, encrypted separation and atomic failure");
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet journal invariant"); }
    private static void Reject(Action action, string code)
    {
        try { action(); throw new InvalidOperationException("Expected " + code); }
        catch (AccountException error) when (error.Code == code) { }
    }
}
