using System.Threading.Channels;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata.Calls;

internal sealed record RoomCallStatus(string Status, string? CallId, long Generation, string? MeetUri, string? Reason,
    bool AuthorizationRequired, int PollAfterMs);

/// <summary>Room-scoped work survives an HTTP disconnect, but is bounded and joined by the service lifetime.</summary>
internal sealed class MeetCallCoordinator(AuthenticatedRooms rooms, MeetGrantRepository grants,
    MeetCallJournal journal, IMeetProvider provider) : BackgroundService
{
    private sealed record Work(RoomMemberAccess? Scope, MeetCallEntry Call, string Kind);
    private readonly object gate = new();
    private readonly HashSet<string> inFlight = new(StringComparer.Ordinal);
    private Task? shutdown;
    private readonly Channel<Work> queue = Channel.CreateBounded<Work>(new BoundedChannelOptions(MeetPolicy.MaximumQueuedCalls)
    { FullMode = BoundedChannelFullMode.Wait, SingleReader = false, SingleWriter = false });

    internal RoomCallStatus Get(RoomMemberAccess scope)
    {
        Need(scope);
        lock (gate) { return View(scope, journal.Latest(scope)); }
    }
    internal RoomCallStatus Start(RoomMemberAccess scope, string requestId)
    {
        Need(scope, owner: true); AccountWire.Need(AccountWire.Token(requestId));
        lock (gate)
        {
            AccountWire.Need(shutdown is null, "unavailable", 503);
            if (!grants.HasAuthorization(scope.Principal)) return View(scope, journal.Latest(scope));
            var known = journal.Latest(scope);
            if (known?.State is "created" or "createRejected")
            {
                // An explicit recovery can only re-read this known space, irrespective of a new native request ID.
                Queue(scope, known, "read"); return View(scope, journal.Latest(scope));
            }
            var (call, dispatch) = journal.Begin(scope, requestId);
            if (dispatch || call.State is "created" or "createRejected") Queue(scope, call, dispatch ? "create" : "read");
            return View(scope, journal.Latest(scope));
        }
    }
    internal RoomCallStatus End(RoomMemberAccess scope, string id, long generation, string requestId)
    {
        Need(scope, owner: true); AccountWire.Need(AccountWire.Token(id) && AccountWire.Token(requestId) && generation is > 0 and <= Wire.MaximumCounter);
        lock (gate)
        {
            AccountWire.Need(shutdown is null, "unavailable", 503);
            var call = journal.Latest(scope);
            AccountWire.Need(call is not null && call.Id == id && call.Generation == generation, "conflict", 409);
            if (call.EndRequestId == requestId) return View(scope, call);
            AccountWire.Need(call.State is "ready" or "endFailed", "conflict", 409);
            if (!grants.HasAuthorization(scope.Principal)) return View(scope, call);
            var checking = call with { State = "checkingEnd", EndRequestId = requestId, Conference = null, Reason = null };
            AccountWire.Need(journal.Update(call, checking), "conflict", 409);
            Queue(scope, checking, "end"); return View(scope, journal.Latest(scope));
        }
    }
    private void Queue(RoomMemberAccess? scope, MeetCallEntry call, string kind)
    {
        if (!inFlight.Add(call.Id)) return;
        if (queue.Writer.TryWrite(new(scope, call, kind))) return;
        inFlight.Remove(call.Id);
        journal.Update(call, call with { State = kind is "end" or "cleanup" ? "endFailed" : kind == "create" ? "createFailed" : "createRejected", Reason = "providerBusy" });
    }
    protected override Task ExecuteAsync(CancellationToken stoppingToken) => Task.WhenAll(
        Enumerable.Range(0, MeetPolicy.CallWorkers).Select(_ => Worker(stoppingToken)).Append(MonitorClosures(stoppingToken)));
    private async Task MonitorClosures(CancellationToken stop)
    {
        try
        {
            while (!stop.IsCancellationRequested)
            {
                try { SweepClosures(); }
                catch (Exception) { Console.Error.WriteLine("Meet room closure could not be scheduled."); }
                await Task.Delay(MeetPolicy.CallPollMilliseconds, stop);
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
    }
    internal void SweepClosures()
    {
        foreach (var call in journal.ClosureCandidates())
        {
            // Room sweeping only holds the room lock. Journal and provider work happen after it returns.
            if (rooms.HasCurrentOwner(call.RoomId, call.Epoch, call.OwnerAccountId)) continue;
            lock (gate)
            {
                if (shutdown is not null || inFlight.Contains(call.Id) || queue.Reader.Count >= MeetPolicy.MaximumQueuedCalls) continue;
                var checking = BeginClosure(call);
                if (checking is not null) Queue(null, checking, "cleanup");
            }
        }
    }
    private MeetCallEntry? BeginClosure(MeetCallEntry call)
    {
        if (!MeetCallJournal.CanClose(call) || journal.Find(call.Id) != call) return null;
        var checking = call with
        {
            State = "checkingEnd",
            EndRequestId = AccountWire.Secret(),
            Reason = null,
            ClosureAttempted = true
        };
        return journal.Update(call, checking) ? checking : null;
    }
    private async Task Worker(CancellationToken stop)
    {
        try
        {
            await foreach (var work in queue.Reader.ReadAllAsync(stop))
            {
                try
                {
                    if (work.Kind is "end" or "cleanup") await EndWork(work, stop);
                    else await CreateWork(work, stop);
                }
                catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
                catch (Exception)
                {
                    // Retain the last durable phase. Without a known response, never dispatch this mutation again.
                    Console.Error.WriteLine("Meet operation did not complete.");
                }
                finally
                {
                    lock (gate)
                    {
                        try { RecoverInterrupted(work); }
                        catch (Exception) { Console.Error.WriteLine("Meet operation state could not be saved."); }
                        inFlight.Remove(work.Call.Id);
                    }
                }
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
    }
    private async Task CreateWork(Work work, CancellationToken stop)
    {
        var call = work.Call;
        if (work.Kind == "create")
        {
            if (!rooms.IsCurrent(work.Scope!)) { journal.Update(call, call with { State = "createFailed", Reason = "roomChanged" }); return; }
            try
            {
                MeetSpace space = await provider.Create(call.OwnerAccountId, stop);
                var created = call with { State = "created", Space = space.Name, MeetUri = space.MeetUri };
                if (!journal.Update(call, created)) return;
                call = created;
            }
            catch (MeetProviderException error)
            {
                journal.Update(call, call with { State = error.Ambiguous ? "createUnknown" : "createFailed", Reason = error.Ambiguous ? "providerOutcomeUnknown" : error.Code }); return;
            }
        }
        if (!rooms.IsCurrent(work.Scope!)) { journal.Update(call, call with { State = "orphaned", Reason = "roomChanged" }); return; }
        try
        {
            MeetSpace verified = await provider.Read(call.OwnerAccountId, call.Space!, stop);
            if (verified.Name != call.Space || verified.MeetUri != call.MeetUri) throw new MeetProviderException("providerResponseInvalid");
            if (!rooms.IsCurrent(work.Scope!)) { journal.Update(call, call with { State = "orphaned", Reason = "roomChanged" }); return; }
            journal.Update(call, call with { State = "ready", Conference = verified.Conference, Reason = null });
        }
        catch (MeetProviderException error) { journal.Update(call, call with { State = "createRejected", Reason = error.Code }); }
    }
    private async Task EndWork(Work work, CancellationToken stop)
    {
        var call = work.Call;
        if (work.Kind != "cleanup" && !rooms.IsCurrent(work.Scope!)) { journal.Update(call, call with { State = "orphaned", Reason = "roomChanged" }); return; }
        MeetSpace current;
        try { current = await provider.Read(call.OwnerAccountId, call.Space!, stop); }
        catch (MeetProviderException error) { journal.Update(call, call with { State = "endFailed", Reason = error.Code }); return; }
        if (current.Name != call.Space || current.MeetUri != call.MeetUri)
        { journal.Update(call, call with { State = "endFailed", Reason = "providerResponseInvalid" }); return; }
        if (work.Kind == "cleanup" && call.Conference is not null && current.Conference is not null && current.Conference != call.Conference)
        { journal.Update(call, call with { State = "endFailed", Reason = "roomChanged" }); return; }
        if (work.Kind != "cleanup" && !rooms.IsCurrent(work.Scope!)) { journal.Update(call, call with { State = "orphaned", Reason = "roomChanged" }); return; }
        if (current.Conference is null) { journal.Update(call, call with { State = "ended", Reason = null }); return; }
        var ending = call with { State = "ending", Conference = current.Conference };
        if (!journal.Update(call, ending)) return;
        try
        {
            await provider.End(call.OwnerAccountId, call.Space!, stop);
            journal.Update(ending, ending with { State = "ended", Reason = null });
        }
        catch (MeetProviderException error)
        { journal.Update(ending, ending with { State = error.Ambiguous ? "endUnknown" : "endFailed", Reason = error.Ambiguous ? "providerOutcomeUnknown" : error.Code }); }
    }
    private void RecoverInterrupted(Work work)
    {
        var call = journal.Find(work.Call.Id);
        if (call?.Id != work.Call.Id) return;
        if (call?.State == "creating") journal.Update(call, call with { State = "createUnknown", Reason = "providerOutcomeUnknown" });
        else if (call?.State == "checkingEnd") journal.Update(call, call with { State = "endFailed", Reason = "providerUnavailable" });
        else if (call?.State == "ending") journal.Update(call, call with { State = "endUnknown", Reason = "providerOutcomeUnknown" });
    }
    private void Need(RoomMemberAccess scope, bool owner = false) => AccountWire.Need((!owner || scope.Owner) && rooms.IsCurrent(scope), "forbidden", 403);
    private RoomCallStatus View(RoomMemberAccess scope, MeetCallEntry? call)
    {
        bool permission = scope.Owner && !grants.HasAuthorization(scope.Principal);
        if (call is null) return new("idle", null, 0, null, null, permission, MeetPolicy.CallPollMilliseconds);
        string state = call.State switch
        {
            "created" or "creating" => "creating",
            "createRejected" or "createFailed" => "creationFailed",
            "createUnknown" => "creationUnknown",
            "checkingEnd" => "ending",
            "orphaned" => "unavailable",
            _ => call.State
        };
        return new(state, call.Id, call.Generation, call.State == "ready" ? call.MeetUri : null, call.Reason, permission, MeetPolicy.CallPollMilliseconds);
    }
    public override Task StopAsync(CancellationToken cancellationToken)
    {
        lock (gate) return shutdown ??= StopCore(cancellationToken);
    }
    private async Task StopCore(CancellationToken cancellationToken)
    {
        queue.Writer.TryComplete(); await base.StopAsync(cancellationToken);
        if (ExecuteTask is { IsCompleted: false }) return;
        lock (gate)
        {
            foreach (var id in inFlight.ToArray())
            {
                var call = journal.Find(id);
                if (call is not null) RecoverInterrupted(new(null, call, "interrupted"));
                inFlight.Remove(id);
            }
        }
        // Workers are joined before shutdown cleanup, including a create that returned during cancellation.
        await Parallel.ForEachAsync(journal.ClosureCandidates(), new ParallelOptions
        { MaxDegreeOfParallelism = MeetPolicy.CallWorkers, CancellationToken = cancellationToken }, async (call, cancel) =>
        {
            Work work;
            lock (gate)
            {
                if (inFlight.Contains(call.Id)) return;
                var checking = BeginClosure(call);
                if (checking is null) return;
                inFlight.Add(call.Id); work = new(null, checking, "cleanup");
            }
            try { await EndWork(work, cancel); }
            catch (Exception) { Console.Error.WriteLine("Meet shutdown cleanup did not complete."); }
            finally
            {
                lock (gate) { RecoverInterrupted(work); inFlight.Remove(call.Id); }
            }
        });
    }
}
