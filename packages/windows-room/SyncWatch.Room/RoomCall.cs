using System.Text.Json;

namespace SyncWatch.Room;

public sealed record RoomCallStatus(string State, string? CallId, long Generation, string? MeetUri, string? Reason, bool AuthorizationRequired, long PollAfterMs)
{
    public override string ToString() => "[room call state redacted]";
    public static RoomCallStatus Decode(ReadOnlyMemory<byte> bytes)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "status", "callId", "generation", "meetUri", "reason", "authorizationRequired", "pollAfterMs");
        string state = RoomWire.String(value, "status"); string? id = RoomWire.OptionalString(value.GetProperty("callId")), uri = RoomWire.OptionalString(value.GetProperty("meetUri")), reason = RoomWire.OptionalString(value.GetProperty("reason"));
        long generation = RoomWire.Integer(value, "generation"), poll = RoomWire.Integer(value, "pollAfterMs", 10000);
        var permission = value.GetProperty("authorizationRequired");
        if (permission.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || poll < 1000
            || !new[] { "idle", "creating", "ready", "creationFailed", "creationUnknown", "ending", "ended", "endFailed", "endUnknown", "unavailable" }.Contains(state)
            || (id is null) != (state == "idle") || (generation == 0) != (state == "idle") || (uri is not null) != (state == "ready")) throw RoomWire.Bad();
        if (id is not null) _ = AccountClient.Token(value.GetProperty("callId"));
        RoomWire.ValidateMeetUri(uri);
        if (state is "creationFailed" or "endFailed") { if (reason is null || !new[] { "permissionRequired", "providerBusy", "providerUnavailable", "providerResponseInvalid", "providerPolicyInvalid", "roomChanged" }.Contains(reason)) throw RoomWire.Bad(); }
        else if (state is "creationUnknown" or "endUnknown") { if (reason != "providerOutcomeUnknown") throw RoomWire.Bad(); }
        else if (state == "unavailable") { if (reason != "roomChanged") throw RoomWire.Bad(); }
        else if (reason is not null) throw RoomWire.Bad();
        return new(state, id, generation, uri, reason, permission.GetBoolean(), poll);
    }
}
public sealed class RoomCallOperation
{
    internal string RequestId { get; } = AccountClient.Secret();
    internal string RoomId { get; }
    internal string MemberId { get; }
    internal string Epoch { get; }
    internal string Action { get; }
    internal string? CallId { get; }
    internal long Generation { get; }
    internal Guid Scope { get; }
    internal RoomCallOperation(RoomAdmission admission, Guid scope, string action, RoomCallStatus? status)
    { RoomId = admission.RoomId; MemberId = admission.MemberId; Epoch = admission.Bootstrap.Snapshot.Epoch; Scope = scope; Action = action; CallId = status?.CallId; Generation = status?.Generation ?? 0; }
    public override string ToString() => "[room call operation redacted]";
}
public sealed partial class RoomClient
{
    public async Task<RoomCallStatus> CallStatusAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        _ = NeedAccount();
        return RoomCallStatus.Decode(await RequestAsync(HttpMethod.Get, "v2/rooms/" + admission.RoomId + "/call", null, null, cancellation, admission: admission));
    }
    public RoomCallOperation CallStartOperation(RoomAdmission admission) => CallOperation(admission, "start", null);
    public RoomCallOperation CallEndOperation(RoomAdmission admission, RoomCallStatus status)
    {
        if (status.CallId is null || status.Generation <= 0 || status.State is not ("ready" or "endFailed")) throw RoomWire.Bad();
        return CallOperation(admission, "end", status);
    }
    private RoomCallOperation CallOperation(RoomAdmission admission, string action, RoomCallStatus? status)
    {
        var authority = NeedAccount(); if (admission.AccountScope != authority.Scope) throw RoomWire.Bad();
        return new(admission, authority.Scope, action, status);
    }
    public async Task<RoomCallStatus> RequestCallAsync(RoomCallOperation operation, RoomAdmission admission, CancellationToken cancellation = default)
    {
        var authority = NeedAccount();
        if (operation.Scope != authority.Scope || operation.RoomId != admission.RoomId || operation.MemberId != admission.MemberId || operation.Epoch != admission.Bootstrap.Snapshot.Epoch) throw RoomWire.Bad();
        Dictionary<string, object> body = new() { ["v"] = 2, ["requestId"] = operation.RequestId };
        if (operation.CallId is not null) { body["callId"] = operation.CallId; body["generation"] = operation.Generation; }
        return RoomCallStatus.Decode(await RequestAsync(HttpMethod.Post, "v2/rooms/" + admission.RoomId + "/call/" + operation.Action, null, RoomWire.Encode(body), cancellation, admission: admission));
    }
}
