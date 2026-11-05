using System.Text.Json;

namespace SyncWatch.Room;

public sealed partial class RoomClient
{
    private RoomAccountAuthority NeedAccount() => Authority ?? throw RoomWire.Bad();
    public async Task<RoomAdmission> CreateForAccountAsync(string run, string requestId, CancellationToken cancellation = default)
    {
        var authority = NeedAccount(); RoomWire.Id(run); RoomWire.Id(requestId);
        byte[] body = RoomWire.Encode(new { v = 2, clientRunId = run, requestId });
        return RoomAccessWire.Admission(await RequestAsync(HttpMethod.Post, "v2/rooms", null, body, cancellation), run, authority.Scope);
    }
    public async Task<RoomInvitation> InviteAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        _ = NeedAccount();
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Post, $"v2/rooms/{admission.RoomId}/invitation", null, null, cancellation, admission: admission));
        var value = document.RootElement; RoomWire.Keys(value, "roomId", "invitation", "expiresIn");
        if (RoomWire.String(value, "roomId") != admission.RoomId || RoomWire.Integer(value, "expiresIn", 86400) == 0) throw RoomWire.Bad();
        return new(admission.RoomId, RoomAccessWire.Secret(value, "invitation"));
    }
    public async Task<RoomAdmissionAttempt> RequestAdmissionAsync(RoomInvitation invitation, string run, string requestId, CancellationToken cancellation = default)
    {
        var authority = NeedAccount(); RoomWire.Id(run); RoomWire.Id(requestId);
        if (!System.Text.RegularExpressions.Regex.IsMatch(invitation.Credential.Value, "\\A[0-9A-F]{64}\\z")) throw RoomWire.Bad();
        byte[] body = RoomWire.Encode(new { v = 2, clientRunId = run, requestId, invitation = invitation.Credential.Value });
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Post, $"v2/rooms/{invitation.RoomId}/admission-requests", null, body, cancellation));
        var request = RoomAdmissionRequest.Decode(document.RootElement);
        if (request.Account.Id != authority.AccountId) throw RoomWire.Bad();
        return new(invitation.RoomId, request, requestId, run, authority.Scope);
    }
    private RoomAccountAuthority Validate(RoomAdmissionAttempt attempt)
    {
        var authority = NeedAccount(); if (attempt.Scope != authority.Scope) throw RoomWire.Bad(); return authority;
    }
    public async Task<RoomAdmissionRequest> AdmissionStatusAsync(RoomAdmissionAttempt attempt, CancellationToken cancellation = default)
    {
        var authority = Validate(attempt);
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Get, $"v2/room-admissions/{attempt.RequestId}", null, null, cancellation, run: attempt.RunId));
        var value = RoomAdmissionRequest.Decode(document.RootElement);
        if (value.Id != attempt.Request.Id || value.Account.Id != authority.AccountId) throw RoomWire.Bad();
        return value;
    }
    public async Task<RoomAdmission> AcceptAsync(RoomAdmissionAttempt attempt, CancellationToken cancellation = default)
    {
        var authority = Validate(attempt);
        byte[] body = RoomWire.Encode(new { v = 2, clientRunId = attempt.RunId });
        return RoomAccessWire.Admission(await RequestAsync(HttpMethod.Post, $"v2/room-admissions/{attempt.RequestId}/accept", null, body, cancellation), attempt.RunId, authority.Scope, attempt.RoomId);
    }
    public async Task<IReadOnlyList<RoomAdmissionRequest>> PendingAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        _ = NeedAccount();
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Get, $"v2/rooms/{admission.RoomId}/pending", null, null, cancellation, admission: admission));
        var value = document.RootElement;
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > 64) throw RoomWire.Bad();
        var requests = value.EnumerateArray().Select(RoomAdmissionRequest.Decode).ToArray();
        if (requests.Select(p => p.Id).Distinct(StringComparer.Ordinal).Count() != requests.Length || requests.Any(p => p.Status != "pending")) throw RoomWire.Bad();
        return Array.AsReadOnly(requests);
    }
    public async Task DecideAsync(RoomAdmission admission, string approvalId, bool approve, CancellationToken cancellation = default)
    {
        _ = NeedAccount(); RoomWire.Id(approvalId);
        byte[] body = RoomWire.Encode(new { v = 2, approvalId, approve });
        RoomAccessWire.Status(await RequestAsync(HttpMethod.Post, $"v2/rooms/{admission.RoomId}/decision", null, body, cancellation, admission: admission), "decided");
    }
    public async Task RevokeAsync(RoomAdmission admission, string memberId, CancellationToken cancellation = default)
    {
        _ = NeedAccount(); RoomWire.Id(memberId);
        byte[] body = RoomWire.Encode(new { v = 2, memberId });
        RoomAccessWire.Status(await RequestAsync(HttpMethod.Post, $"v2/rooms/{admission.RoomId}/revoke", null, body, cancellation, admission: admission), "revoked");
    }
    public async Task<RoomSyncTicket> SyncTicketAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        _ = NeedAccount();
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Post, $"v2/rooms/{admission.RoomId}/sync-ticket", null, null, cancellation, admission: admission));
        var value = document.RootElement; RoomWire.Keys(value, "ticket", "expiresIn");
        long seconds = RoomWire.Integer(value, "expiresIn", 60); if (seconds == 0) throw RoomWire.Bad();
        return new(RoomAccessWire.Secret(value, "ticket"), seconds);
    }
}
