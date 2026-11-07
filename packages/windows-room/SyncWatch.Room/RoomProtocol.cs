using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SyncWatch.Room;

public sealed record RoomIdentity
{
    public string ClientRunId { get; }
    public string SyncplayUsername { get; }
    public RoomIdentity(string clientRunId, string syncplayUsername)
    {
        RoomWire.Id(clientRunId); RoomWire.Text(syncplayUsername, 64);
        ClientRunId = clientRunId; SyncplayUsername = syncplayUsername;
    }
}
public sealed record RoomMember(string MemberId, RoomIdentity Identity);
public sealed record RoomConnection
{
    public string Host { get; }
    public int Port { get; }
    public string Room { get; }
    public RoomConnection(string host, int port, string room)
    {
        RoomWire.Text(host, 253); RoomWire.Text(room, 64);
        if (port is < 1 or > 65535 || !Regex.IsMatch(host, "\\A[A-Za-z0-9.:-]+\\z", RegexOptions.CultureInvariant)) throw RoomWire.Bad();
        Host = host; Port = port; Room = room;
    }
}
public sealed record RoomSnapshot(string Epoch, long Sequence, long WatchGeneration, string WatchStatus, string? MediaId,
    IReadOnlyList<RoomMember> Members, string? MeetUri);
public sealed record RoomBootstrap(RoomSnapshot Snapshot, RoomConnection Connection, string CatalogCursor, IReadOnlyList<string> MissingMembers)
{
    public static RoomBootstrap InitialEvent(ReadOnlyMemory<byte> bytes, RoomAdmission admission)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "v", "type", "data");
        if (RoomWire.Integer(value, "v", 1) != 1 || RoomWire.String(value, "type") != "room.bootstrap") throw RoomWire.Bad();
        var result = RoomWire.Bootstrap(value.GetProperty("data")); var expected = admission.Bootstrap;
        if (result.Snapshot.Epoch != expected.Snapshot.Epoch || result.Connection != expected.Connection
            || result.Snapshot.Members.FirstOrDefault(p => p.MemberId == admission.MemberId) != expected.Snapshot.Members.FirstOrDefault(p => p.MemberId == admission.MemberId)) throw RoomWire.Bad();
        return result;
    }
    public static RoomBootstrap Decode(ReadOnlyMemory<byte> bytes)
    { using var document = RoomWire.Parse(bytes); return RoomWire.Bootstrap(document.RootElement); }
}
public sealed record RoomAdmission(string RoomId, string MemberId, RoomSecret? Token, RoomSecret? Invitation,
    DateTimeOffset ExpiresAt, RoomBootstrap Bootstrap)
{
    internal Guid? AccountScope { get; init; }
    public bool IsAccountBacked => AccountScope is not null;
    public static RoomAdmission Decode(ReadOnlyMemory<byte> bytes, RoomIdentity identity, bool owner)
    {
        using var document = RoomWire.Parse(bytes); var value = document.RootElement;
        RoomWire.Keys(value, "roomId", "memberId", "token", "invitation", "expiresAt", "bootstrap");
        string room = RoomWire.String(value, "roomId"), member = RoomWire.String(value, "memberId");
        RoomWire.Id(room); RoomWire.Id(member);
        var bootstrap = RoomWire.Bootstrap(value.GetProperty("bootstrap"));
        if (bootstrap.Snapshot.Members.FirstOrDefault(item => item.MemberId == member)?.Identity != identity) throw RoomWire.Bad();
        string? invitation = RoomWire.OptionalString(value.GetProperty("invitation"));
        if ((invitation is not null) != owner || !DateTimeOffset.TryParse(RoomWire.String(value, "expiresAt"),
            CultureInfo.InvariantCulture, DateTimeStyles.None, out var expires)) throw RoomWire.Bad();
        return new(room, member, new(RoomWire.String(value, "token")), invitation is null ? null : new(invitation), expires, bootstrap);
    }
}

internal static class RoomWire
{
    internal const int MaximumBytes = 65536;
    internal const long MaximumCounter = 9007199254740991;
    internal static RoomProtocolException Bad() => new("malformed");
    internal static void Id(string value)
    { if (!Regex.IsMatch(value, "\\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\\z", RegexOptions.CultureInvariant)) throw Bad(); }
    internal static void Text(string value, int maximum)
    {
        try { _ = new UTF8Encoding(false, true).GetByteCount(value); } catch (EncoderFallbackException) { throw Bad(); }
        if (value.EnumerateRunes().Count() is < 1 || value.EnumerateRunes().Count() > maximum || value.Any(c => c < 32 || c == 127)) throw Bad();
    }
    internal static void Keys(JsonElement value, params string[] names)
    {
        if (value.ValueKind != JsonValueKind.Object || !value.EnumerateObject().Select(p => p.Name).ToHashSet(StringComparer.Ordinal).SetEquals(names)) throw Bad();
    }
    internal static void ValidateMeetUri(string? value)
    {
        if (value is not null && !Regex.IsMatch(value, "\\Ahttps://meet\\.google\\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}\\z", RegexOptions.CultureInvariant)) throw Bad();
    }
    internal static string String(JsonElement value, string name) => value.GetProperty(name).ValueKind == JsonValueKind.String ? value.GetProperty(name).GetString()! : throw Bad();
    internal static string? OptionalString(JsonElement value) => value.ValueKind == JsonValueKind.Null ? null : value.ValueKind == JsonValueKind.String ? value.GetString()! : throw Bad();
    internal static long Integer(JsonElement value, string name, long maximum = MaximumCounter)
    {
        var item = value.GetProperty(name);
        if (item.ValueKind != JsonValueKind.Number || !item.TryGetInt64(out long number) || number < 0 || number > maximum) throw Bad();
        return number;
    }
    internal static RoomBootstrap Bootstrap(JsonElement value)
    {
        Keys(value, "snapshot", "connection", "catalogCursor", "missingMembers");
        var snapshot = Snapshot(value.GetProperty("snapshot")); var connection = value.GetProperty("connection");
        Keys(connection, "host", "port", "room", "requireTls");
        var missingValue = value.GetProperty("missingMembers");
        if (connection.GetProperty("requireTls").ValueKind != JsonValueKind.True || missingValue.ValueKind != JsonValueKind.Array || missingValue.GetArrayLength() > 64) throw Bad();
        var missing = missingValue.EnumerateArray().Select(p => OptionalString(p) ?? throw Bad()).ToArray();
        if (missing.Distinct(StringComparer.Ordinal).Count() != missing.Length || missing.Any(id => !snapshot.Members.Any(p => p.MemberId == id))) throw Bad();
        string cursor = String(value, "catalogCursor"); Text(cursor, 128);
        return new(snapshot, new(String(connection, "host"), (int)Integer(connection, "port", 65535), String(connection, "room")), cursor, Array.AsReadOnly(missing));
    }
    private static RoomSnapshot Snapshot(JsonElement value)
    {
        Keys(value, "v", "roomEpoch", "sequence", "watchGeneration", "watch", "members", "meetUri");
        var members = value.GetProperty("members");
        if (Integer(value, "v", 1) != 1 || members.ValueKind != JsonValueKind.Array || members.GetArrayLength() is < 1 or > 64) throw Bad();
        string epoch = String(value, "roomEpoch"); Id(epoch);
        var bindings = members.EnumerateArray().Select(item =>
        {
            Keys(item, "memberId", "clientRunId", "syncplayUsername"); string id = String(item, "memberId"); Id(id);
            return new RoomMember(id, new(String(item, "clientRunId"), String(item, "syncplayUsername")));
        }).ToArray();
        if (bindings.Select(p => p.MemberId).Distinct(StringComparer.Ordinal).Count() != bindings.Length
            || bindings.Select(p => p.Identity.SyncplayUsername).Distinct(StringComparer.Ordinal).Count() != bindings.Length) throw Bad();
        var watch = value.GetProperty("watch"); Keys(watch, "status", "mediaId");
        string status = String(watch, "status"); string? media = OptionalString(watch.GetProperty("mediaId"));
        if (status is not ("idle" or "selected" or "active" or "ended") || (media is null) != (status == "idle")) throw Bad();
        if (media is not null) Id(media);
        string? meet = OptionalString(value.GetProperty("meetUri"));
        ValidateMeetUri(meet);
        return new(epoch, Integer(value, "sequence"), Integer(value, "watchGeneration"), status, media, Array.AsReadOnly(bindings), meet);
    }
    internal static byte[] Encode(object value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value);
        if (bytes.Length > MaximumBytes) throw new RoomProtocolException("frameTooLarge");
        return bytes;
    }
    internal static JsonDocument Parse(ReadOnlyMemory<byte> bytes)
    {
        if (bytes.Length > MaximumBytes) throw new RoomProtocolException("frameTooLarge");
        JsonDocument? document = null;
        try
        {
            _ = new UTF8Encoding(false, true).GetString(bytes.Span);
            document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 32 });
            CheckTree(document.RootElement); return document;
        }
        catch { document?.Dispose(); throw Bad(); }
    }
    private static void CheckTree(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            HashSet<string> keys = new(StringComparer.Ordinal);
            foreach (var property in value.EnumerateObject()) { if (!keys.Add(property.Name)) throw Bad(); CheckTree(property.Value); }
        }
        else if (value.ValueKind == JsonValueKind.Array) { foreach (var item in value.EnumerateArray()) CheckTree(item); }
        else if (value.ValueKind == JsonValueKind.String) { _ = value.GetString(); }
        else if (value.ValueKind == JsonValueKind.Number && (!value.TryGetInt64(out long number) || number < 0 || number > MaximumCounter)) throw Bad();
    }
}
