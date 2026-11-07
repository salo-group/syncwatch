using System.Text.Json;
using System.Text.RegularExpressions;
using SyncWatch.Domain;

namespace SyncWatch.Bridge;

public enum HelperReplyKind { Ready, Applied, Stopped }

/// <summary>A typed response to one pending command; provider text is not retained.</summary>
public sealed record HelperReply(HelperReplyKind? Kind, string? ErrorCode = null, bool Retryable = false, string? Revision = null)
{
    public static HelperReply Decode(ReadOnlyMemory<byte> frame, PlaybackScope wireScope, ulong commandId,
        HelperReplyKind expected, string? expectedRevision = null, bool requireAdmission = false)
    {
        if (commandId is 0 or > PlaybackScope.MaximumGeneration || !Enum.IsDefined(expected)) throw Bad();
        using var document = PlayerCorrection.DecodeFrame(frame);
        var value = document.RootElement;
        PlayerCorrection.Keys(value, ["v", "replyTo", "type", "clientRunId", "mediaGeneration", "payload"]);
        if (PlayerCorrection.Integer(value.GetProperty("v"), 1) != 1 || PlayerCorrection.Text(value, "replyTo") != "cmd-" + commandId) throw Bad();
        wireScope.Check(PlayerCorrection.Text(value, "clientRunId"), PlayerCorrection.Integer(value.GetProperty("mediaGeneration"), PlaybackScope.MaximumGeneration));
        var payload = value.GetProperty("payload");
        string kind = PlayerCorrection.Text(value, "type");
        if (kind == "bridge.error")
        {
            PlayerCorrection.Keys(payload, ["code", "message", "retryable"]);
            string code = PlayerCorrection.Text(payload, "code"), message = PlayerCorrection.Text(payload, "message");
            var retryable = payload.GetProperty("retryable");
            if (!ErrorCodes.Contains(code) || message.EnumerateRunes().Count() is < 1 or > 512 || message.Any(c => c < 32 || c == 127)
                || retryable.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw Bad();
            return new(null, code, retryable.GetBoolean());
        }
        switch (expected)
        {
            case HelperReplyKind.Ready:
                PlayerCorrection.Keys(payload, ["syncplayRevision"], ["admissionVersion"]);
                if (payload.TryGetProperty("admissionVersion", out var capability)) { if (PlayerCorrection.Integer(capability, 1) != 1) throw Bad(); }
                else if (requireAdmission) throw Bad();
                if (kind != "bridge.ready" || expectedRevision is null || !Regex.IsMatch(expectedRevision, "\\A[0-9a-f]{40}\\z", RegexOptions.CultureInvariant)
                    || PlayerCorrection.Text(payload, "syncplayRevision") != expectedRevision) throw Bad();
                break;
            case HelperReplyKind.Applied:
                PlayerCorrection.Keys(payload, ["status"]);
                if (kind != "bridge.result" || PlayerCorrection.Text(payload, "status") != "applied") throw Bad();
                break;
            case HelperReplyKind.Stopped:
                PlayerCorrection.Keys(payload, []);
                if (kind != "bridge.stopped") throw Bad();
                break;
        }
        return new(expected, Revision: expected == HelperReplyKind.Ready ? expectedRevision : null);
    }
    private static PlayerCorrectionException Bad() => new("malformed");
    private static readonly HashSet<string> ErrorCodes = ["malformed", "frameTooLarge", "unsupportedVersion", "foreignRun", "staleGeneration", "staleEpoch", "sequenceGap", "duplicate", "conflict", "invalidState", "queueFull", "unavailable", "timeout", "cancelled", "internal"];
}
