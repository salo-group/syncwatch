using System.Diagnostics;
using SyncWatch;
using SyncWatch.Domain;
using SyncWatch.Room;

internal static class RoomEventMonitorChecks
{
    internal static async Task RunAsync(string address, string key)
    {
        await using var client = new RoomClient(new(address));
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(20));
        Task? monitoring = null, publishing = null;
        try
        {
            var identity = new RoomIdentity("run-" + Guid.NewGuid().ToString("N"), "Windows recovery");
            var admission = await client.CreateAsync(identity, new("syncplay.example.com", 8999, "Recovery"), new(key), cancellation.Token);
            var owner = new RoomMembershipSession(client, admission, identity);
            var media = new MediaDescriptor("movie", "Movie", "movie", 10, 60000, null);
            _ = await owner.PublishAsync([media], cancellation.Token);
            var corpus = Enumerable.Range(0, 120).Select(index => new MediaDescriptor("extra-" + index, "Extra", "extra", 10, 60000, null));
            cancellation.CancelAfter(TimeSpan.FromSeconds(49));
            publishing = owner.PublishAsync(new[] { media }.Concat(corpus).ToArray(), cancellation.Token);
            int recoveries = 0, deliveries = 0;
            var outage = new Stopwatch();
            var recovered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            monitoring = RoomEventMonitor.RunAsync(new(address), owner, () => recoveries++, view =>
            {
                if (!view.Bootstrap.Snapshot.Members.Any(p => p.MemberId == admission.MemberId && p.Identity == identity)) throw new Exception("Recovery replaced identity");
                if (!outage.IsRunning) outage.Start();
                if (++deliveries <= 6 || outage.Elapsed < TimeSpan.FromSeconds(18)) throw new RoomProtocolException("transport");
                recovered.TrySetResult();
            }, cancellation.Token);
            await publishing; publishing = null;
            Task completed = await Task.WhenAny(recovered.Task, monitoring).WaitAsync(cancellation.Token);
            await completed;
            if (recoveries == 0) throw new Exception("Event transport was not rebound");
            cancellation.CancelAfter(TimeSpan.FromSeconds(20));
            _ = await owner.PublishAsync([media], cancellation.Token);
            var context = await owner.WatchContextAsync(cancellation.Token);
            var selected = await owner.PerformAsync(new RoomCommandAction.Select(media.MediaId), context, cancellation.Token);
            if (selected.Definition.Selection?.Media != media) throw new Exception("Recovery reset command numbering");
            await cancellation.CancelAsync();
            try { await monitoring; } catch (OperationCanceledException) { }
            monitoring = null;
            int exhaustedAttempts = 0, exhaustedRetries = 0;
            var exhaustion = new Stopwatch();
            using var exhaustionLimit = new CancellationTokenSource(TimeSpan.FromSeconds(49));
            try
            {
                await RoomEventMonitor.RunAsync(new(address), owner, () =>
                {
                    if (!exhaustion.IsRunning) exhaustion.Start();
                    exhaustedRetries++;
                },
                    _ => { exhaustedAttempts++; throw new RoomProtocolException("transport"); }, exhaustionLimit.Token);
                throw new Exception("Transport retries were not bounded");
            }
            catch (Exception error) when (!exhaustionLimit.IsCancellationRequested && RoomController.IsMetadataInterruption(error)) { }
            if (exhaustedRetries is < 1 or > 8 || exhaustedAttempts > exhaustedRetries + 1 || exhaustion.Elapsed >= TimeSpan.FromSeconds(28))
                throw new Exception("Recovery exceeded its attempt budget");
            int retries = 0;
            try
            {
                await RoomEventMonitor.RunAsync(new(address), owner, () => retries++,
                    _ => throw new RoomProtocolException("malformed"), CancellationToken.None);
                throw new Exception("Malformed metadata was retried");
            }
            catch (RoomProtocolException error) when (error.Message == "malformed") { }
            if (retries != 0) throw new Exception("Invalid metadata triggered recovery");
            await client.LeaveAsync(admission);
            Console.WriteLine("SYNCWATCH_ROOM_EVENT_RECOVERY_OK retained identity/commands, terminal metadata, cancel/join");
        }
        finally
        {
            await cancellation.CancelAsync();
            if (publishing is not null) { try { await publishing; } catch (OperationCanceledException) { } }
            if (monitoring is not null) { try { await monitoring; } catch (OperationCanceledException) { } }
        }
    }
}
