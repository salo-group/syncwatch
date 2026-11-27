using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using SyncWatch.Domain;

namespace SyncWatch.Bridge;

public sealed class PlayerCorrectionException(string code) : IOException(code);
public enum CorrectionKind { Pause, Seek, Rate, KeepLoaded }

/// <summary>Validated helper request. Wire identity never becomes native ownership.</summary>
public sealed class PlayerCorrection
{
    public string Id { get; }
    public ulong AfterUserAction { get; }
    public CorrectionKind Kind { get; }
    public bool Paused { get; }
    public long PositionMs { get; }
    public double Rate { get; }
    public string? MediaHandle { get; }
    private readonly PlaybackScope wireScope;
    internal bool SameRequest(PlayerCorrection other) => Id == other.Id && AfterUserAction == other.AfterUserAction
        && Kind == other.Kind && Paused == other.Paused && PositionMs == other.PositionMs && Rate == other.Rate
        && MediaHandle == other.MediaHandle && wireScope == other.wireScope;
    private PlayerCorrection(string id, ulong after, CorrectionKind kind, PlaybackScope scope,
        bool paused = false, long position = 0, double rate = 1, string? handle = null)
    { Id = id; AfterUserAction = after; Kind = kind; wireScope = scope; Paused = paused; PositionMs = position; Rate = rate; MediaHandle = handle; }

    public static PlayerCorrection Decode(ReadOnlyMemory<byte> frame, PlaybackScope wireScope)
    {
        using var document = DecodeFrame(frame);
        var value = document.RootElement;
        Keys(value, ["v", "id", "type", "clientRunId", "mediaGeneration", "payload"], ["afterUserAction"]);
        if (Integer(value.GetProperty("v"), 1) != 1) throw Bad();
        wireScope.Check(Text(value, "clientRunId"), Integer(value.GetProperty("mediaGeneration"), PlaybackScope.MaximumGeneration));
        string id = Text(value, "id");
        if (!Regex.IsMatch(id, "\\Acmd-[1-9][0-9]{0,15}\\z", RegexOptions.CultureInvariant)) throw Bad();
        ulong after = value.TryGetProperty("afterUserAction", out var watermark) ? Integer(watermark, PlaybackScope.MaximumGeneration) : 0;
        var payload = value.GetProperty("payload");
        switch (Text(value, "type"))
        {
            case "player.setPaused":
                Keys(payload, ["paused"]);
                var paused = payload.GetProperty("paused");
                if (paused.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw Bad();
                return new(id, after, CorrectionKind.Pause, wireScope, paused: paused.GetBoolean());
            case "player.setPosition":
                Keys(payload, ["positionMs"]);
                return new(id, after, CorrectionKind.Seek, wireScope, position: (long)Integer(payload.GetProperty("positionMs"), 604800000));
            case "player.setSpeed":
                Keys(payload, ["rate"]);
                double rate = Number(payload.GetProperty("rate"));
                if (rate is < .25 or > 4) throw Bad();
                return new(id, after, CorrectionKind.Rate, wireScope, rate: rate);
            case "player.openFile":
                Keys(payload, ["mediaHandle"]);
                string handle = Text(payload, "mediaHandle");
                _ = new PlaybackScope(handle, 0);
                return new(id, after, CorrectionKind.KeepLoaded, wireScope, handle: handle);
            default: throw Bad();
        }
    }

    /// <summary>The coordinator checks its action fence before and after this await.</summary>
    public async Task<PlaybackObservation> ApplyAsync(IPlaybackPort player, PlaybackScope nativeScope,
        string authorizedHandle, CancellationToken cancellation = default)
    {
        cancellation.ThrowIfCancellationRequested();
        switch (Kind)
        {
            case CorrectionKind.Pause: await player.SetPausedAsync(Paused, nativeScope, cancellation).ConfigureAwait(false); break;
            case CorrectionKind.Seek: await player.SeekAsync(PositionMs, nativeScope, cancellation).ConfigureAwait(false); break;
            case CorrectionKind.Rate: await player.SetRateAsync(Rate, nativeScope, cancellation).ConfigureAwait(false); break;
            case CorrectionKind.KeepLoaded:
                if (MediaHandle != authorizedHandle) throw new PlayerCorrectionException("unauthorizedHandle");
                break; // The selection owner already loaded this file; never reopen or rewind.
        }
        var wait = Stopwatch.StartNew();
        while (true)
        {
            cancellation.ThrowIfCancellationRequested();
            var observation = await player.ObserveAsync(nativeScope, cancellation).ConfigureAwait(false);
            observation.Validate();
            nativeScope.Check(observation.Scope.ClientRunId, observation.Scope.MediaGeneration);
            bool reachedTarget = (Kind, observation.Status) switch
            {
                (CorrectionKind.Pause, PlaybackStatus.Ready) => observation.Paused == Paused,
                (CorrectionKind.Seek, PlaybackStatus.Ready) => Math.Abs(observation.PositionMs - PositionMs) <= 250,
                (CorrectionKind.Pause or CorrectionKind.Seek, PlaybackStatus.Loading) => false,
                (_, PlaybackStatus.Ready) => true,
                _ => throw new PlayerCorrectionException("unavailable")
            };
            if (reachedTarget) return observation;
            if (wait.Elapsed >= TimeSpan.FromSeconds(2)) throw new PlayerCorrectionException("unavailable");
            await Task.Delay(20, cancellation).ConfigureAwait(false);
        }
    }

    public byte[] Result(PlaybackObservation observation, PlaybackScope nativeScope)
    {
        Validate(observation, nativeScope);
        return Reply("player.result", new { status = "applied", positionMs = observation.PositionMs, paused = observation.Paused });
    }
    // Send only after the newer actual user frame has been published successfully.
    public byte[] CancelledReply() => Reply("bridge.error", new { code = "cancelled", message = "cancelled", retryable = false });
    private byte[] Reply(string type, object payload) => JsonSerializer.SerializeToUtf8Bytes(new
    { v = 1, replyTo = Id, type, clientRunId = wireScope.ClientRunId, mediaGeneration = wireScope.MediaGeneration, payload }).Concat(new byte[] { 10 }).ToArray();
    private static void Validate(PlaybackObservation value, PlaybackScope nativeScope)
    {
        value.Validate(); nativeScope.Check(value.Scope.ClientRunId, value.Scope.MediaGeneration);
        if (value.Status != PlaybackStatus.Ready) throw new PlayerCorrectionException("unavailable");
    }
    private static PlayerCorrectionException Bad() => new("malformed");
    internal static string Text(JsonElement value, string name) => value.GetProperty(name).ValueKind == JsonValueKind.String ? value.GetProperty(name).GetString()! : throw Bad();
    private static double Number(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out double number) || !double.IsFinite(number)) throw Bad();
        return number;
    }
    internal static ulong Integer(JsonElement value, ulong maximum)
    {
        double number = Number(value);
        if (number < 0 || number > maximum || Math.Truncate(number) != number) throw Bad();
        return (ulong)number;
    }
    internal static void Keys(JsonElement value, string[] required, string[]? optional = null)
    {
        if (value.ValueKind != JsonValueKind.Object) throw Bad();
        HashSet<string> keys = [];
        foreach (var field in value.EnumerateObject())
            if (!keys.Add(field.Name) || !required.Contains(field.Name) && !(optional?.Contains(field.Name) ?? false)) throw Bad();
        if (required.Any(key => !keys.Contains(key))) throw Bad();
    }
    internal static JsonDocument DecodeFrame(ReadOnlyMemory<byte> frame)
    {
        var bytes = frame.Span;
        if (bytes.Length is < 2 or > 65536 || bytes[^1] != 10 || bytes[..^1].Contains((byte)10) || bytes.Contains((byte)13)) throw Bad();
        _ = new UTF8Encoding(false, true).GetString(bytes);
        return JsonDocument.Parse(frame, new JsonDocumentOptions { MaxDepth = 32 });
    }
}
