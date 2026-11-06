using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;
using SyncWatch.RoomMetadata.Rooms;

internal static class MeetCallClosureChecks
{
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
    private sealed class Provider : IMeetProvider
    {
        internal int Ends, Reads;
        internal bool FailRead, UnknownEnd;
        internal string? Conference = "conferenceRecords/current-conference";
        public Task<MeetSpace> Create(string account, CancellationToken cancel) => Task.FromResult(Space());
        public Task<MeetSpace> Read(string account, string name, CancellationToken cancel)
        {
            Interlocked.Increment(ref Reads);
            if (FailRead) throw new MeetProviderException("permissionRequired");
            return Task.FromResult(Space());
        }
        public Task End(string account, string name, CancellationToken cancel)
        {
            Interlocked.Increment(ref Ends);
            if (UnknownEnd) throw new MeetProviderException("providerUnavailable", true);
            return Task.CompletedTask;
        }
        private MeetSpace Space() => new("spaces/closure-space", "https://meet.google.com/abc-defg-hij", Conference);
    }
    internal static async Task Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet closure protection requires Windows");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-closure-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var protection = DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")), builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Closure.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
            var clock = new Clock();
            using var accounts = new AccountRepository(root, protection, clock);
            using var grants = new MeetGrantRepository(accounts, root, protection);
            using var journal = new MeetCallJournal(root, protection);
            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999,
                new RoomLimits { RoomLifetime = TimeSpan.FromMinutes(1), DisconnectGrace = TimeSpan.FromDays(1) }, clock);
            var provider = new Provider();
            using var coordinator = new MeetCallCoordinator(rooms, grants, journal, provider);
            await coordinator.StartAsync(default);
            (RoomMemberAccess Scope, DeviceSession Device) Room(string tag)
            {
                var device = accounts.SignIn(tag, "Owner", "owner@example.invalid");
                var principal = accounts.Authorize(device.AccessToken);
                var admission = rooms.Create(principal, "Owner", tag, Wire.NewId("create-"));
                grants.Remember(principal, tag, "synthetic-refresh", managedDomain: true);
                return (rooms.Access(principal, admission.RoomId, admission.MemberId, tag, true), device);
            }
            async Task Ready(RoomMemberAccess scope)
            { _ = coordinator.Start(scope, AccountWire.Secret()); await Until(() => coordinator.Get(scope).Status == "ready"); }
            async Task Closed(RoomMemberAccess scope, string state)
            {
                coordinator.SweepClosures();
                await Until(() => journal.Latest(scope)?.State == state);
                Require(journal.Latest(scope)!.ClosureAttempted);
                int ends = provider.Ends, reads = provider.Reads;
                for (int i = 0; i < 5; i++) coordinator.SweepClosures();
                Require(provider.Ends == ends && provider.Reads == reads);
            }
            try
            {
                var first = Room("close-owner-leave"); await Ready(first.Scope);
                coordinator.SweepClosures(); Require(provider.Ends == 0 && !journal.Latest(first.Scope)!.ClosureAttempted);
                rooms.Leave(first.Scope.Principal, first.Scope.RoomId, first.Scope.MemberId, first.Scope.ClientRunId);
                await Closed(first.Scope, "ended"); Require(provider.Ends == 1);

                var logout = Room("close-owner-logout"); await Ready(logout.Scope);
                accounts.Logout(logout.Device.RefreshToken);
                await Closed(logout.Scope, "ended"); Require(provider.Ends == 2);

                var expiry = Room("close-owner-expiry"); await Ready(expiry.Scope);
                clock.Now = clock.Now.AddMinutes(2);
                await Closed(expiry.Scope, "ended"); Require(provider.Ends == 3);

                var failed = Room("close-read-denied"); await Ready(failed.Scope);
                provider.FailRead = true;
                rooms.Leave(failed.Scope.Principal, failed.Scope.RoomId, failed.Scope.MemberId, failed.Scope.ClientRunId);
                await Closed(failed.Scope, "endFailed"); Require(provider.Ends == 3 && journal.Latest(failed.Scope)!.Reason == "permissionRequired");
                provider.FailRead = false;

                var unknown = Room("close-unknown-end"); await Ready(unknown.Scope);
                provider.UnknownEnd = true;
                rooms.Leave(unknown.Scope.Principal, unknown.Scope.RoomId, unknown.Scope.MemberId, unknown.Scope.ClientRunId);
                await Closed(unknown.Scope, "endUnknown"); Require(provider.Ends == 4);
                provider.UnknownEnd = false; provider.Conference = "conferenceRecords/later-conference";
                coordinator.SweepClosures(); Require(provider.Ends == 4);

                provider.Conference = "conferenceRecords/observed-before-close";
                var replaced = Room("close-conference-replaced"); await Ready(replaced.Scope);
                provider.Conference = "conferenceRecords/replacement-after-close";
                rooms.Leave(replaced.Scope.Principal, replaced.Scope.RoomId, replaced.Scope.MemberId, replaced.Scope.ClientRunId);
                await Closed(replaced.Scope, "endFailed");
                Require(provider.Ends == 4 && journal.Latest(replaced.Scope) is
                { Reason: "roomChanged", Conference: "conferenceRecords/observed-before-close" });

                var empty = Room("close-no-conference"); await Ready(empty.Scope);
                provider.Conference = null;
                rooms.Leave(empty.Scope.Principal, empty.Scope.RoomId, empty.Scope.MemberId, empty.Scope.ClientRunId);
                await Closed(empty.Scope, "ended"); Require(provider.Ends == 4);

                var shutdown = Room("close-service-shutdown"); await Ready(shutdown.Scope);
                provider.Conference = "conferenceRecords/shutdown-conference";
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await coordinator.StopAsync(timeout.Token);
                Require(journal.Latest(shutdown.Scope) is { State: "ended", ClosureAttempted: true } && provider.Ends == 5);
                await coordinator.StopAsync(timeout.Token); Require(provider.Ends == 5);
            }
            finally
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await coordinator.StopAsync(timeout.Token);
            }
            // A surviving known space belongs to an absent room after restart; no native session is revived.
            var restart = Room("close-service-restart");
            var (creating, _) = journal.Begin(restart.Scope, AccountWire.Secret());
            var created = creating with { State = "created", Space = "spaces/closure-space", MeetUri = "https://meet.google.com/abc-defg-hij" };
            Require(journal.Update(creating, created));
            var ready = created with { State = "ready" }; Require(journal.Update(created, ready));
            var restartedRooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999);
            using var restarted = new MeetCallCoordinator(restartedRooms, grants, journal, provider);
            await restarted.StartAsync(default);
            try { await Until(() => journal.Latest(restart.Scope) is { State: "ended", ClosureAttempted: true }); Require(provider.Ends == 6); }
            finally { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5)); await restarted.StopAsync(timeout.Token); }
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_CALL_CLOSURE_OK owner leave/logout/expiry, failed permission, no ambiguous retry, empty conference, shutdown/restart and joined cleanup");
    }
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(10, timeout.Token);
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet closure invariant"); }
}
