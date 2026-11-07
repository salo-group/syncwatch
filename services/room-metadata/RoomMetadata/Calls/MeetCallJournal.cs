using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata.Calls;

internal sealed record MeetCallEntry(string Id, string RoomId, string Epoch, string OwnerAccountId, long Generation,
    string RequestId, string State, string? Space = null, string? MeetUri = null, string? Conference = null,
    string? EndRequestId = null, string? Reason = null, bool ClosureAttempted = false, string? RejectedGrantVersion = null)
{
    public override string ToString() => "[Meet operation redacted]";
}

/// <summary>Durable at-most-once dispatch decisions. An interrupted mutation is unknown, never queued for replay.</summary>
internal sealed class MeetCallJournal : IDisposable
{
    private sealed record State(int Version, MeetCallEntry[] Calls);
    private readonly object gate = new();
    private readonly ProtectedMeetStore<State> store;
    internal MeetCallJournal(string directory, IDataProtectionProvider protection)
    {
        store = new(directory, "calls", protection, () => new(1, []), Valid);
        try
        {
            MeetCallEntry[] recovered = store.Current.Calls.Select(call => call.State switch
            {
                "creating" => call with { State = "createUnknown", Reason = "providerOutcomeUnknown" },
                "ending" => call with { State = "endUnknown", Reason = "providerOutcomeUnknown" },
                "checkingEnd" => call with { State = "endFailed", Conference = null, Reason = "providerUnavailable" },
                _ => call
            }).ToArray();
            if (!recovered.SequenceEqual(store.Current.Calls)) store.Save(new(1, recovered));
        }
        catch { store.Dispose(); throw; }
    }
    private static bool Valid(State state) => state.Version == 1 && state.Calls is { Length: <= MeetPolicy.MaximumCalls }
        && state.Calls.All(call => call is not null && AccountWire.Token(call.Id) && Wire.Id(call.RoomId) && Wire.Id(call.Epoch)
            && AccountWire.Token(call.OwnerAccountId) && AccountWire.Token(call.RequestId) && call.Generation is > 0 and <= Wire.MaximumCounter
            && (call.EndRequestId is null || AccountWire.Token(call.EndRequestId))
            && (call.RejectedGrantVersion is null || AccountWire.Token(call.RejectedGrantVersion))
            && (call.Conference is null || MeetPolicy.ConferenceName(call.Conference))
            && (!call.ClosureAttempted || call.EndRequestId is not null
                && call.State is "checkingEnd" or "ending" or "ended" or "endFailed" or "endUnknown")
            && (call.Reason is null || Reasons.Contains(call.Reason)) && ValidPhase(call))
        && state.Calls.Select(call => call.Id).Distinct(StringComparer.Ordinal).Count() == state.Calls.Length
        && state.Calls.Select(call => (call.RoomId, call.Epoch, call.Generation)).Distinct().Count() == state.Calls.Length
        && state.Calls.Select(call => (call.OwnerAccountId, call.RequestId)).Distinct().Count() == state.Calls.Length;
    private static readonly string[] Reasons = ["permissionRequired", "providerBusy", "providerUnavailable", "providerResponseInvalid", "providerPolicyInvalid", "providerOutcomeUnknown", "roomChanged"];
    private static bool ValidPhase(MeetCallEntry call)
    {
        bool space = MeetPolicy.SpaceName(call.Space) && MeetPolicy.MeetUri(call.MeetUri);
        return call.State switch
        {
            "creating" => call.Space is null && call.MeetUri is null && call.Conference is null && call.EndRequestId is null && call.Reason is null,
            "createFailed" or "createUnknown" => call.Space is null && call.MeetUri is null && call.Conference is null && call.EndRequestId is null && call.Reason is not null,
            "created" or "ready" => space && call.EndRequestId is null && call.Reason is null,
            "createRejected" => space && call.EndRequestId is null && call.Reason is not null,
            "ending" => space && call.EndRequestId is not null && call.Conference is not null && call.Reason is null,
            "checkingEnd" => space && call.EndRequestId is not null && call.Reason is null,
            "ended" => space && call.Reason is null,
            "endFailed" => space && call.EndRequestId is not null && call.Reason is not null,
            "endUnknown" => space && call.EndRequestId is not null && call.Conference is not null && call.Reason is not null,
            "orphaned" => space && call.Reason == "roomChanged",
            _ => false
        };
    }
    internal MeetCallEntry? Latest(RoomMemberAccess scope)
    {
        lock (gate) return store.Current.Calls.Where(call => Belongs(call, scope)).MaxBy(call => call.Generation);
    }
    internal MeetCallEntry? Find(string id)
    {
        lock (gate) return store.Current.Calls.SingleOrDefault(call => call.Id == id);
    }
    internal MeetCallEntry[] ClosureCandidates()
    {
        lock (gate) return store.Current.Calls.Where(CanClose).ToArray();
    }
    internal static bool CanClose(MeetCallEntry call) => !call.ClosureAttempted
        && call.State is "created" or "createRejected" or "ready" or "orphaned" or "endFailed";
    internal (MeetCallEntry Call, bool Dispatch) Begin(RoomMemberAccess scope, string requestId)
    {
        AccountWire.Need(scope.Owner, "forbidden", 403); AccountWire.Need(AccountWire.Token(requestId));
        lock (gate)
        {
            var retry = store.Current.Calls.SingleOrDefault(call => call.OwnerAccountId == scope.OwnerAccountId && call.RequestId == requestId);
            if (retry is not null) { AccountWire.Need(Belongs(retry, scope), "conflict", 409); return (retry, false); }
            var previous = Latest(scope);
            AccountWire.Need(previous is null || previous.State is "ended" or "createFailed", "conflict", 409);
            AccountWire.Need(store.Current.Calls.Length < MeetPolicy.MaximumCalls, "queueFull", 503);
            long generation = (previous?.Generation ?? 0) + 1;
            AccountWire.Need(generation <= Wire.MaximumCounter, "queueFull", 503);
            var next = new MeetCallEntry(AccountWire.Secret(), scope.RoomId, scope.RoomEpoch, scope.OwnerAccountId, generation, requestId, "creating");
            store.Save(new(1, [.. store.Current.Calls, next]));
            return (next, true);
        }
    }
    internal bool Update(MeetCallEntry expected, MeetCallEntry replacement)
    {
        AccountWire.Need(expected.Id == replacement.Id && expected.RoomId == replacement.RoomId && expected.Epoch == replacement.Epoch
            && expected.OwnerAccountId == replacement.OwnerAccountId && expected.Generation == replacement.Generation && expected.RequestId == replacement.RequestId
            && (!expected.ClosureAttempted || replacement.ClosureAttempted)
            && (expected.ClosureAttempted == replacement.ClosureAttempted || replacement.State == "checkingEnd"));
        lock (gate)
        {
            var current = store.Current.Calls.SingleOrDefault(call => call.Id == expected.Id);
            if (current != expected) return false;
            AccountWire.Need(expected.State switch
            {
                "creating" => replacement.State is "created" or "createFailed" or "createUnknown",
                "created" or "createRejected" => replacement.State is "ready" or "createRejected" or "orphaned"
                    || replacement.State == "checkingEnd" && replacement.ClosureAttempted,
                "ready" or "endFailed" => replacement.State is "checkingEnd" or "ended" or "orphaned",
                "checkingEnd" => replacement.State is "ending" or "ended" or "endFailed" or "orphaned",
                "ending" => replacement.State is "ended" or "endFailed" or "endUnknown",
                "endUnknown" => replacement.State is "ended" or "orphaned",
                "orphaned" => replacement.State == "ended" || replacement.State == "checkingEnd" && replacement.ClosureAttempted,
                _ => false
            }, "conflict", 409);
            store.Save(new(1, store.Current.Calls.Select(call => call.Id == expected.Id ? replacement : call).ToArray()));
            return true;
        }
    }
    private static bool Belongs(MeetCallEntry call, RoomMemberAccess scope) => call.RoomId == scope.RoomId
        && call.Epoch == scope.RoomEpoch && call.OwnerAccountId == scope.OwnerAccountId;
    public void Dispose() => store.Dispose();
}
