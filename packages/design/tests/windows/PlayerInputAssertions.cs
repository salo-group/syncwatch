using System.IO;
using System.Text.Json;
using SyncWatch.Design;

internal static class PlayerInputAssertions
{
    internal static void Run()
    {
        foreach (var (message, value, expectedKey) in new (int, long, string?)[]
        {
            (0x0100, 0x20, "Space"), (0x0100, 0x4D, "KeyM"), (0x0100, 0x46, "KeyF"),
            (0x0100, 0x25, "ArrowLeft"), (0x0100, 0x27, "ArrowRight"),
            (0x0100, 0x26, "ArrowUp"), (0x0100, 0x28, "ArrowDown"), (0x0100, 0x1B, "Escape"),
            (0x0102, ' ', "Space"), (0x0102, 'm', "KeyM"), (0x0102, 'M', "KeyM"),
            (0x0102, 'f', "KeyF"), (0x0102, 'F', "KeyF"),
            (0x0100, 0xE7, null), (0x0101, 0x20, null), (0x0102, '%', null),
            (0x0102, 0x1B, null), (0x0102, '\u043C', null), (0x0104, 0x46, null),
        })
            if (NativePlayerInput.InputKey(message, value) != expectedKey)
                throw new InvalidOperationException("Native keyboard transport mapping");
        using var data = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "player-input.json")));
        var defaults = data.RootElement.GetProperty("defaults");
        int count = 0;
        foreach (var row in data.RootElement.GetProperty("cases").EnumerateArray())
        {
            JsonElement Value(string key) => row.TryGetProperty(key, out var value) ? value : defaults.GetProperty(key);
            bool Flag(string key) => row.TryGetProperty(key, out var value) && value.GetBoolean();
            var model = new PlayerPresentation
            {
                State = Enum.Parse<PlayerVisualState>(Value("state").GetString()!, true),
                PositionMs = Value("positionMs").GetInt64(),
                DurationMs = Value("durationMs").GetInt64(),
                Volume = Value("volume").GetDouble(),
                SkipSeconds = Value("skipSeconds").GetInt32(),
                EnabledActions = Value("enabledActions").EnumerateArray().Select(a => a.GetString()!).ToHashSet(),
            };
            var received = new List<(string Action, double? Value)>();
            void Emit(string action, double? value) => received.Add((action, value));
            bool handled = row.GetProperty("kind").GetString() == "wheel"
                ? model.DispatchWheel(row.GetProperty("wheel").GetDouble(), Emit, Flag("modified"), Flag("protectedFocus"))
                : model.DispatchKey(row.GetProperty("key").GetString()!, Emit, Flag("repeating"), Flag("fullscreen"), Flag("modified"), Flag("protectedFocus"));
            var expected = row.GetProperty("expected").EnumerateArray().Select(e =>
                (Action: e.GetProperty("action").GetString()!, Value: e.TryGetProperty("value", out var v) ? (double?)v.GetDouble() : null)).ToArray();
            if (handled != row.GetProperty("handled").GetBoolean() || received.Count != expected.Length
                || received.Zip(expected).Any(pair => pair.First.Action != pair.Second.Action
                    || (pair.First.Value is double actual && pair.Second.Value is double value ? Math.Abs(actual - value) > .000001 : pair.First.Value != pair.Second.Value)))
                throw new InvalidOperationException("Player input: " + row.GetProperty("name").GetString());
            if (model.Volume != Value("volume").GetDouble() || model.PositionMs != Value("positionMs").GetInt64())
                throw new InvalidOperationException("Input acknowledged itself");
            count++;
        }
        if (count != 34) throw new InvalidOperationException("Incomplete input corpus");
        foreach (double value in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
            if (PlayerPresentation.Gallery(PlayerVisualState.Paused).DispatchWheel(value, (_, _) => throw new InvalidOperationException("Nonfinite wheel emitted input")))
                throw new InvalidOperationException("Nonfinite wheel was consumed");
    }
}
