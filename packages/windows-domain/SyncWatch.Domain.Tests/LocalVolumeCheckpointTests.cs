using System.Text.Json;
using SyncWatch.Domain;

internal static class LocalVolumeCheckpointTests
{
    internal static void Run(string fixtures)
    {
        using var document = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(fixtures, "local-volume-checkpoint.json")));
        var cases = document.RootElement.GetProperty("cases");
        if (cases.GetArrayLength() != 9) throw new InvalidOperationException("Incomplete local volume corpus");
        foreach (var row in cases.EnumerateArray())
        {
            var model = new LocalVolumeCheckpoint(new("run-1", 2));
            foreach (var step in row.GetProperty("steps").EnumerateArray())
            {
                if (step.GetProperty("op").GetString() == "submitted") { model.Submitted(); continue; }
                string? owner = step.TryGetProperty("owner", out var scope) ? scope.GetString() : null;
                string? status = step.TryGetProperty("status", out var state) ? state.GetString() : null;
                var volume = step.GetProperty("volume");
                var observation = new PlaybackObservation
                {
                    Scope = new(owner == "foreignRun" ? "run-2" : "run-1", owner == "oldGeneration" ? 1UL : 2UL),
                    Sequence = step.GetProperty("sequence").GetUInt64(),
                    Status = status == "loading" ? PlaybackStatus.Loading : status == "failed" ? PlaybackStatus.Failed : PlaybackStatus.Ready,
                    DurationMs = 60_000, Paused = true,
                    Volume = volume.ValueKind == JsonValueKind.Null ? null : volume.GetDouble(),
                    Muted = step.TryGetProperty("muted", out var muted) && muted.GetBoolean(),
                };
                var expected = step.GetProperty("expected");
                double? result = expected.ValueKind == JsonValueKind.Null ? null : expected.GetDouble();
                if (model.Observe(observation) != result) throw new InvalidOperationException(row.GetProperty("name").GetString());
            }
        }
    }
}
