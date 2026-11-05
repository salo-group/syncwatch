using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SyncWatch.Room;

public sealed record RoomAdmissionRequest(string Id, string Status, AccountProfile Account)
{
    internal static RoomAdmissionRequest Decode(JsonElement value)
    {
        RoomWire.Keys(value, "id", "status", "accountId", "name", "email");
        string id = RoomWire.String(value, "id"), status = RoomWire.String(value, "status");
        RoomWire.Id(id);
        if (status is not ("pending" or "approved" or "denied" or "admitted" or "expired" or "revoked" or "closed")) throw RoomWire.Bad();
        var account = new AccountProfile(RoomWire.String(value, "accountId"), RoomWire.String(value, "name"), RoomWire.String(value, "email"));
        if (!Regex.IsMatch(account.Id, "\\A[A-Za-z0-9_-]{43}\\z", RegexOptions.CultureInvariant)) throw RoomWire.Bad();
        RoomWire.Text(account.Name, 128); RoomWire.Text(account.Email, 320);
        return new(id, status, account);
    }
}

/// <summary>The guest retains its own correlation ID; the owner's approval ID is distinct.</summary>
public sealed class RoomAdmissionAttempt
{
    public string RoomId { get; }
    public RoomAdmissionRequest Request { get; }
    internal string RequestId { get; }
    internal string RunId { get; }
    internal Guid Scope { get; }
    internal RoomAdmissionAttempt(string room, RoomAdmissionRequest request, string requestId, string run, Guid scope)
    { RoomId = room; Request = request; RequestId = requestId; RunId = run; Scope = scope; }
}

public sealed record RoomSyncTicket(RoomSecret Credential, long ExpiresIn);

internal static class RoomAccessWire
{
    internal static byte[] Result(byte[] bytes)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "v", "result");
        if (RoomWire.Integer(value, "v", 2) != 2) throw RoomWire.Bad();
        var result = value.GetProperty("result");
        if (result.ValueKind is not (JsonValueKind.Object or JsonValueKind.Array)) throw RoomWire.Bad();
        return RoomWire.Encode(result);
    }
    internal static void Status(byte[] bytes, string expected)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "status"); if (RoomWire.String(value, "status") != expected) throw RoomWire.Bad();
    }
    internal static RoomSecret Secret(JsonElement value, string name)
    {
        string text = RoomWire.String(value, name);
        if (!Regex.IsMatch(text, "\\A[0-9A-F]{64}\\z", RegexOptions.CultureInvariant)) throw RoomWire.Bad();
        return new(text);
    }
    internal static RoomAdmission Admission(byte[] bytes, string run, Guid scope, string? expectedRoom = null)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "roomId", "memberId", "expiresAt", "bootstrap");
        string room = RoomWire.String(value, "roomId"), member = RoomWire.String(value, "memberId");
        RoomWire.Id(room); RoomWire.Id(member);
        var bootstrap = RoomWire.Bootstrap(value.GetProperty("bootstrap"));
        string time = RoomWire.String(value, "expiresAt");
        if (expectedRoom is not null && expectedRoom != room || bootstrap.Snapshot.Members.FirstOrDefault(p => p.MemberId == member)?.Identity.ClientRunId != run
            || time.Length > 64 || !DateTimeOffset.TryParse(time, CultureInfo.InvariantCulture, DateTimeStyles.None, out var expires)) throw RoomWire.Bad();
        return new(room, member, null, null, expires, bootstrap) { AccountScope = scope };
    }
}
