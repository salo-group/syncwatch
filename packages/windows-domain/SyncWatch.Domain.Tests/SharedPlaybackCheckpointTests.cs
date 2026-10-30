using System.Text.Json;
using SyncWatch.Domain;

internal static class SharedPlaybackCheckpointTests
{
    internal static void Run(string fixtures)
    {
        using var document = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(fixtures, "shared-playback-checkpoint.json")));
        var cases = document.RootElement.GetProperty("cases");
        if (cases.GetArrayLength() != 18) throw new InvalidOperationException("Incomplete checkpoint corpus");
        foreach (var row in cases.EnumerateArray())
        {
            var model = new SharedPlaybackCheckpoint(new("run-1", 2));
            foreach (var step in row.GetProperty("steps").EnumerateArray())
            {
                switch (step.GetProperty("op").GetString())
                {
                    case "interrupt": model.Interrupt(); break;
                    case "clear": model.Clear(); break;
                    case "observe":
                        string? owner = step.TryGetProperty("scope", out var scope) ? scope.GetString() : null;
                        var observation = new PlaybackObservation
                        {
                            Scope = new(owner == "foreignRun" ? "run-2" : "run-1", owner == "oldGeneration" ? 1UL : 2UL),
                            Sequence = step.TryGetProperty("sequence", out var sequence) ? sequence.GetUInt64() : 1,
                            Status = step.TryGetProperty("status", out var status) ? Enum.Parse<PlaybackStatus>(status.GetString()!, true) : PlaybackStatus.Ready,
                            PositionMs = step.TryGetProperty("positionMs", out var position) ? position.GetInt64() : 49000,
                            DurationMs = step.TryGetProperty("durationMs", out var duration) ? duration.ValueKind == JsonValueKind.Null ? null : duration.GetInt64() : 60000,
                            Paused = !step.TryGetProperty("paused", out var paused) || paused.GetBoolean(),
                            Seekable = !step.TryGetProperty("seekable", out var seekable) || seekable.GetBoolean(),
                            Buffering = step.TryGetProperty("buffering", out var buffering) && buffering.GetBoolean(),
                            Ended = step.TryGetProperty("ended", out var ended) && ended.GetBoolean(),
                        };
                        model.Observe(observation, !step.TryGetProperty("available", out var available) || available.GetBoolean());
                        break;
                    default: throw new InvalidOperationException("Unknown checkpoint operation");
                }
            }
            var expected = row.GetProperty("expectedPositionMs");
            if (model.Position(60000) != (expected.ValueKind == JsonValueKind.Null ? (long?)null : expected.GetInt64()) || model.Position(90000) is not null)
                throw new InvalidOperationException(row.GetProperty("name").GetString());
        }
    }
}
