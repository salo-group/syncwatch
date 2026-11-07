using System.Text.Json;
using System.Text.RegularExpressions;
using SyncWatch.Domain;

namespace SyncWatch.Bridge;

/// <summary>Encoding only. The session owner serializes writes and allocates IDs.</summary>
public sealed class HelperCommands(PlaybackScope wireScope)
{
    public byte[] Hello(ulong id) => Frame(id, "bridge.hello", new { supportedVersions = new[] { 1 } });
    public byte[] Shutdown(ulong id) => Frame(id, "bridge.shutdown", new { });
    public byte[] Disconnect(ulong id) => Frame(id, "sync.disconnect", new { });
    public byte[] Ready(ulong id, bool value) => Frame(id, "sync.setReady", new { ready = value });
    public byte[] Connect(ulong id, string host, int port, string room, string username, bool accountAdmission = false)
    {
        if (host.Length is < 1 or > 253 || !Regex.IsMatch(host, "\\A[A-Za-z0-9.:_-]+\\z", RegexOptions.CultureInvariant) || port is < 1 or > 65535) throw Bad();
        Text(room, 256); Text(username, 64);
        var payload = new Dictionary<string, object> { ["host"] = host, ["port"] = port, ["room"] = room, ["username"] = username, ["tls"] = true };
        if (accountAdmission) payload["accountAdmission"] = true;
        return Frame(id, "sync.connect", payload);
    }
    public byte[] Admit(ulong id, string nonce, SyncplayAdmission authority)
    {
        if (!Regex.IsMatch(nonce, "\\A[0-9a-f]{32}\\z", RegexOptions.CultureInvariant)) throw Bad();
        return Frame(id, "sync.admit", new { nonce, ticket = authority.Credential, clientRunId = authority.ClientRunId });
    }
    public byte[] Loaded(ulong id, string handle, string name, long durationMs, ulong fileSizeBytes)
    {
        _ = new PlaybackScope(handle, 0); Text(name, 256);
        if (name.Contains('/') || name.Contains('\\') || durationMs is < 0 or > WatchSession.MaximumPositionMs || fileSizeBytes > PlaybackScope.MaximumGeneration) throw Bad();
        return Frame(id, "player.loaded", new { mediaHandle = handle, name, durationMs, fileSizeBytes });
    }
    public byte[] Observation(ulong id, PlaybackObservation value, PlaybackScope nativeScope) => State(id, value, nativeScope, null);
    // Only an explicit local shared action may publish user authority.
    public byte[] UserObservation(ulong id, ulong sequence, PlaybackObservation value, PlaybackScope nativeScope)
    {
        if (sequence is 0 or > PlaybackScope.MaximumGeneration || value.Status != PlaybackStatus.Ready) throw Bad();
        return State(id, value, nativeScope, sequence);
    }
    private byte[] State(ulong id, PlaybackObservation value, PlaybackScope nativeScope, ulong? sequence)
    {
        value.Validate(); nativeScope.Check(value.Scope.ClientRunId, value.Scope.MediaGeneration);
        return Frame(id, "player.state", new
        {
            status = value.Status.ToString().ToLowerInvariant(),
            positionMs = value.PositionMs,
            durationMs = value.DurationMs,
            paused = value.Paused,
            rate = value.Rate,
            seekable = value.Seekable
        }, sequence);
    }
    private byte[] Frame(ulong id, string type, object payload, ulong? sequence = null)
    {
        if (id is 0 or > PlaybackScope.MaximumGeneration) throw Bad();
        Dictionary<string, object> envelope = new() { ["v"] = 1, ["id"] = "cmd-" + id, ["type"] = type, ["clientRunId"] = wireScope.ClientRunId, ["mediaGeneration"] = wireScope.MediaGeneration, ["payload"] = payload };
        if (type == "player.state") envelope["origin"] = sequence is null ? "observation" : "user";
        if (sequence is ulong userSequence) envelope["actionSequence"] = userSequence;
        byte[] frame = JsonSerializer.SerializeToUtf8Bytes(envelope).Concat(new byte[] { 10 }).ToArray();
        if (frame.Length > 65536) throw Bad();
        return frame;
    }
    private static void Text(string value, int maximum)
    {
        int count = value.EnumerateRunes().Count();
        if (count < 1 || count > maximum || value.Any(c => c < 32 || c == 127)) throw Bad();
        for (int i = 0; i < value.Length; i++)
        {
            if (char.IsHighSurrogate(value[i])) { if (++i == value.Length || !char.IsLowSurrogate(value[i])) throw Bad(); }
            else if (char.IsLowSurrogate(value[i])) throw Bad();
        }
    }
    private static PlayerCorrectionException Bad() => new("malformed");
}
