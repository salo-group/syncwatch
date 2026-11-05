using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;
using SyncWatch.RoomMetadata.Rooms;

internal static class MeetCallCoordinatorChecks
{
    private sealed class Provider : IMeetProvider
    {
        internal int Creates, Reads, Ends;
        internal string? Conference;
        internal TaskCompletionSource<bool>? Entered, Release;
        internal bool FailRead, UnknownEnd;
        internal int Active;
        public async Task<MeetSpace> Create(string account, CancellationToken cancel)
        {
            Interlocked.Increment(ref Creates); Interlocked.Increment(ref Active);
            try { Entered?.TrySetResult(true); if (Release is not null) await Release.Task.WaitAsync(cancel); return Space(); }
            finally { Interlocked.Decrement(ref Active); }
        }
        public Task<MeetSpace> Read(string account, string name, CancellationToken cancel)
        {
            Interlocked.Increment(ref Reads);
            if (FailRead) throw new MeetProviderException("providerPolicyInvalid");
            return Task.FromResult(Space());
        }
        public Task End(string account, string name, CancellationToken cancel)
        {
            Interlocked.Increment(ref Ends);
            if (UnknownEnd) throw new MeetProviderException("providerUnavailable", true);
            return Task.CompletedTask;
        }
        private MeetSpace Space() => new("spaces/owned-space", "https://meet.google.com/abc-defg-hij", Conference);
    }
    internal static async Task Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet coordinator protection requires Windows validation");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-coordinator-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var protection = DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")), builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Coordinator.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
            using var accounts = new AccountRepository(root, protection);
            using var grants = new MeetGrantRepository(accounts, root, protection);
            using var journal = new MeetCallJournal(root, protection);
            var owner = accounts.SignIn("coordinator-owner", "Owner", "owner@example.invalid");
            var guest = accounts.SignIn("coordinator-guest", "Guest", "guest@example.invalid");
            var principal = accounts.Authorize(owner.AccessToken); var other = accounts.Authorize(guest.AccessToken);
            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999);
            RoomMemberAccess Room(string run)
            {
                var admission = rooms.Create(principal, "Owner", run, AccountWire.Secret());
                return rooms.Access(principal, admission.RoomId, admission.MemberId, run, true);
            }
            var first = Room("first-call-run");
            var provider = new Provider { Entered = Signal(), Release = Signal() };
            using var coordinator = new MeetCallCoordinator(rooms, grants, journal, provider);
            await coordinator.StartAsync(default);
            try
            {
                string request = AccountWire.Secret();
                Require(coordinator.Start(first, request) is { Status: "idle", AuthorizationRequired: true } && provider.Creates == 0);
                grants.Remember(principal, "coordinator-owner", "synthetic-refresh");
                var creating = coordinator.Start(first, request); Require(creating.Status == "creating" && creating.MeetUri is null);
                await provider.Entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
                Require(coordinator.Start(first, request).CallId == creating.CallId && provider.Creates == 1);
                rooms.Leave(principal, first.RoomId, first.MemberId, first.ClientRunId);
                provider.Release.SetResult(true);
                await Until(() => journal.Latest(first)?.State == "orphaned");
                Require(provider.Reads == 0 && provider.Creates == 1);
                Reject(() => coordinator.Get(first), "forbidden");

                var second = Room("second-call-run"); provider.Entered = null; provider.Release = null; provider.FailRead = true;
                string secondRequest = AccountWire.Secret();
                _ = coordinator.Start(second, secondRequest);
                await Until(() => coordinator.Get(second).Status == "creationFailed");
                Require(provider.Creates == 2 && coordinator.Get(second).MeetUri is null);
                provider.FailRead = false;
                // A retry verifies the known space and never repeats spaces.create.
                for (int attempt = 0; attempt < 20 && coordinator.Get(second).Status != "ready"; attempt++)
                { _ = coordinator.Start(second, secondRequest); await Task.Delay(10); }
                var ready = coordinator.Get(second); Require(ready.Status == "ready" && ready.MeetUri == "https://meet.google.com/abc-defg-hij" && provider.Creates == 2);
                var invitation = rooms.Invite(principal, second.RoomId, second.MemberId, second.ClientRunId);
                string joinRequest = AccountWire.Secret();
                var pending = rooms.RequestAdmission(other, "Guest", second.RoomId, "call-guest-run", joinRequest, invitation.Invitation);
                rooms.Decide(principal, second.RoomId, second.MemberId, second.ClientRunId, pending.Id, true);
                var joined = rooms.Accept(other, joinRequest, "call-guest-run");
                var member = rooms.Access(other, second.RoomId, joined.MemberId, "call-guest-run");
                Require(coordinator.Get(member).MeetUri == ready.MeetUri);
                Reject(() => coordinator.Start(member, AccountWire.Secret()), "forbidden");
                Reject(() => coordinator.End(member, ready.CallId!, ready.Generation, AccountWire.Secret()), "forbidden");
                var foreign = Room("foreign-call-run");
                Require(coordinator.Get(foreign).MeetUri is null);
                Reject(() => coordinator.End(foreign, ready.CallId!, ready.Generation, AccountWire.Secret()), "conflict");
                Reject(() => coordinator.End(second, ready.CallId!, ready.Generation + 1, AccountWire.Secret()), "conflict");
                provider.Conference = "conferenceRecords/first-conference"; provider.UnknownEnd = true;
                string endingRequest = AccountWire.Secret();
                _ = coordinator.End(second, ready.CallId!, ready.Generation, endingRequest);
                await Until(() => coordinator.Get(second).Status == "endUnknown");
                Require(provider.Ends == 1 && journal.Latest(second)?.Conference == provider.Conference);
                provider.Conference = "conferenceRecords/later-conference";
                Require(coordinator.End(second, ready.CallId!, ready.Generation, endingRequest).Status == "endUnknown");
                Reject(() => coordinator.End(second, ready.CallId!, ready.Generation, AccountWire.Secret()), "conflict");
                Require(provider.Ends == 1);

                var stopping = Room("stopping-call-run"); provider.Entered = Signal(); provider.Release = Signal();
                _ = coordinator.Start(stopping, AccountWire.Secret()); await provider.Entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await coordinator.StopAsync(timeout.Token);
                Require(provider.Active == 0 && journal.Latest(stopping)?.State == "createUnknown");
            }
            finally
            {
                provider.Release?.TrySetResult(true);
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await coordinator.StopAsync(timeout.Token);
            }
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_CALL_COORDINATOR_OK owner/member isolation, queued single dispatch, policy readback retry, stale room, unknown conference end and joined shutdown");
    }
    private static TaskCompletionSource<bool> Signal() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    private static async Task Until(Func<bool> ready)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!ready()) await Task.Delay(10, timeout.Token);
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet coordinator invariant"); }
    private static void Reject(Action action, string code)
    {
        try { action(); throw new InvalidOperationException("Expected " + code); }
        catch (AccountException error) when (error.Code == code) { }
    }
}
