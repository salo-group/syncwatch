using System.Text.Json;
using System.Text.RegularExpressions;

namespace SyncWatch.Room;

public sealed class RoomMeetAuthorization
{
    public Uri BrowserUri { get; }
    public long ExpiresIn { get; }
    public long PollAfterMs { get; }
    internal string Id { get; }
    internal string Proof { get; }
    internal string Confirmation { get; } = AccountClient.Secret();
    internal string RoomId { get; }
    internal string MemberId { get; }
    internal string Epoch { get; }
    internal Guid Scope { get; }
    internal RoomMeetAuthorization(Uri uri, long expires, long poll, string id, string proof, RoomAdmission admission, Guid scope)
    { BrowserUri = uri; ExpiresIn = expires; PollAfterMs = poll; Id = id; Proof = proof; RoomId = admission.RoomId; MemberId = admission.MemberId; Epoch = admission.Bootstrap.Snapshot.Epoch; Scope = scope; }
    public override string ToString() => "[Meet authorization redacted]";
}
public sealed record RoomMeetAuthorizationStatus(string State, AccountProfile? Account, string? Reason)
{
    public static RoomMeetAuthorizationStatus Decode(ReadOnlyMemory<byte> bytes, string accountId)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, ["status", "account", "reason"]);
        string state = RoomWire.String(value, "status");
        string? reason = RoomWire.OptionalString(value.GetProperty("reason"));
        var profile = value.GetProperty("account");
        AccountProfile? account = null;
        if (profile.ValueKind != JsonValueKind.Null)
        {
            RoomWire.Keys(profile, "id", "name", "email");
            _ = RoomWire.String(profile, "id"); _ = RoomWire.String(profile, "name"); _ = RoomWire.String(profile, "email");
            account = AccountClient.Profile(profile);
        }
        if (!new[] { "pending", "authorizing", "browserConfirmationRequired", "confirmAuthorization", "complete", "cancelled", "failed", "expired" }.Contains(state)
            || (account is not null) != (state == "confirmAuthorization") || account is not null && account.Id != accountId) throw RoomWire.Bad();
        if (state == "failed") { if (reason is null || !new[] { "roomChanged", "accountMismatch", "permissionUnavailable", "invalidConfirmation" }.Contains(reason)) throw RoomWire.Bad(); }
        else if (state is "browserConfirmationRequired" or "confirmAuthorization") { if (reason is not null and not "invalidConfirmation") throw RoomWire.Bad(); }
        else if (reason is not null) throw RoomWire.Bad();
        return new(state, account, reason);
    }
}
public sealed partial class RoomClient
{
    public async Task<RoomMeetAuthorization> BeginMeetAuthorizationAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        var authority = NeedAccount(); string proof = AccountClient.Secret();
        var body = RoomWire.Encode(new { v = 2, challenge = AccountClient.Challenge(proof) });
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Post, "v2/rooms/" + admission.RoomId + "/call-authorizations", null, body, cancellation, admission: admission));
        var value = document.RootElement; RoomWire.Keys(value, ["transactionId", "browserUri", "expiresIn", "pollAfterMs"]);
        string id = AccountClient.Token(value.GetProperty("transactionId")); Uri uri = new(Address.Origin, "auth/google/meet-start?id=" + id);
        long expires = RoomWire.Integer(value, "expiresIn", 600), poll = RoomWire.Integer(value, "pollAfterMs", 10000);
        if (RoomWire.String(value, "browserUri") != uri.AbsoluteUri || expires == 0 || poll < 1000) throw RoomWire.Bad();
        return new(uri, expires, poll, id, proof, admission, authority.Scope);
    }
    public Task<RoomMeetAuthorizationStatus> MeetAuthorizationStatusAsync(RoomMeetAuthorization flow, RoomAdmission admission, CancellationToken cancellation = default) => MeetAuthorizationAsync("poll", flow, admission, null, cancellation);
    public Task<RoomMeetAuthorizationStatus> VerifyMeetAuthorizationAsync(RoomMeetAuthorization flow, RoomAdmission admission, string code, CancellationToken cancellation = default) => MeetAuthorizationAsync("verify", flow, admission, code, cancellation);
    public Task<RoomMeetAuthorizationStatus> ConfirmMeetAuthorizationAsync(RoomMeetAuthorization flow, RoomAdmission admission, string code, CancellationToken cancellation = default) => MeetAuthorizationAsync("confirm", flow, admission, code, cancellation);
    public Task<RoomMeetAuthorizationStatus> CancelMeetAuthorizationAsync(RoomMeetAuthorization flow, RoomAdmission admission, CancellationToken cancellation = default) => MeetAuthorizationAsync("cancel", flow, admission, null, cancellation);
    private async Task<RoomMeetAuthorizationStatus> MeetAuthorizationAsync(string action, RoomMeetAuthorization flow, RoomAdmission admission, string? code, CancellationToken cancellation)
    {
        var authority = NeedAccount();
        if (flow.Scope != authority.Scope || flow.RoomId != admission.RoomId || flow.MemberId != admission.MemberId || flow.Epoch != admission.Bootstrap.Snapshot.Epoch) throw RoomWire.Bad();
        Dictionary<string, object> body = new() { ["v"] = 2, ["proof"] = flow.Proof };
        if (code is not null) { if (!Regex.IsMatch(code, "\\A[0-9A-F]{12}\\z", RegexOptions.CultureInvariant)) throw RoomWire.Bad(); body["browserProof"] = code; }
        if (action == "confirm") body["requestId"] = flow.Confirmation;
        return RoomMeetAuthorizationStatus.Decode(await RequestAsync(HttpMethod.Post, "v2/call-authorizations/" + flow.Id + "/" + action, null, RoomWire.Encode(body), cancellation, admission: admission), authority.AccountId);
    }
}
