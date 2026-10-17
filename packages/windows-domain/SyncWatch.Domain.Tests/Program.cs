using System.Text.Json;
using SyncWatch.Domain;

string path = Environment.GetEnvironmentVariable("SYNCWATCH_CONTRACT_FIXTURES")
    ?? throw new InvalidOperationException("Set SYNCWATCH_CONTRACT_FIXTURES to the shared acceptance corpus");
using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(path));
HashSet<string> names = ["playback-ready", "obsolete-media", "unallocated-media", "replaced-helper"];
HashSet<string> tested = [];
foreach (JsonElement fixture in document.RootElement.EnumerateArray())
{
    string name = fixture.GetProperty("name").GetString()!;
    if (!names.Contains(name))
    {
        continue;
    }

    JsonElement context = fixture.GetProperty("context");
    JsonElement value = fixture.GetProperty("value");
    PlaybackScope scope = new(context.GetProperty("clientRunId").GetString()!, context.GetProperty("mediaGeneration").GetUInt64());
    ContractRejection? actual = Capture(() => scope.Check(value.GetProperty("clientRunId").GetString()!, value.GetProperty("mediaGeneration").GetUInt64()));
    string? expected = fixture.GetProperty("error").GetString();
    string? code = actual switch
    {
        ContractRejection.Malformed => "malformed",
        ContractRejection.ForeignRun => "foreignRun",
        ContractRejection.StaleGeneration => "staleGeneration",
        _ => null
    };
    Require(code == expected, $"Shared fixture failed: {name}");
    tested.Add(name);
}

Require(tested.SetEquals(names), "Shared ownership fixtures must all execute");
PlaybackScope original = new("run-7", 3);
PlaybackScope replacement = original.ReplacingMedia();
Require(Capture(() => replacement.Check("run-7", 3)) == ContractRejection.StaleGeneration, "Replacement must reject pending work");
replacement.Check("run-7", 4);
PlaybackScope restarted = PlaybackScope.Create();
Require(Capture(() => restarted.Check(original.ClientRunId, 0)) == ContractRejection.ForeignRun, "Restart must reject an old process");
PlaybackScope last = new("run-7", PlaybackScope.MaximumGeneration);
Require(Capture(() => last.ReplacingMedia()) == ContractRejection.Malformed, "Generation must not overflow");
Require(Capture(() => new PlaybackScope("run-7", ulong.MaxValue)) == ContractRejection.Malformed, "Generation must fit the wire integer range");
foreach (string invalid in new[] { "", "-run", "run/7", "run\n7", "\u00e9", new string('a', 65) })
{
    Require(Capture(() => new PlaybackScope(invalid, 0)) == ContractRejection.Malformed, "Invalid process identity was accepted");
}

Console.WriteLine("Passed shared ownership fixtures, replacement/restart, integer bounds, and identifier checks");
return 0;

static ContractRejection? Capture(Action action)
{
    try
    {
        action();
        return null;
    }
    catch (ContractException error)
    {
        return error.Rejection;
    }
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}
