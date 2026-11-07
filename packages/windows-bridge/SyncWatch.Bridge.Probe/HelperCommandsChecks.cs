using System.Text.Json;
using System.Text.Json.Nodes;
using SyncWatch.Bridge;
using SyncWatch.Domain;

internal static class HelperCommandsChecks
{
    internal static void Run(string path)
    {
        using var corpus = JsonDocument.Parse(File.ReadAllBytes(path));
        var cases = corpus.RootElement.GetProperty("cases");
        if (cases.GetArrayLength() != 31) throw new InvalidDataException("Incomplete outgoing command corpus");
        var encoder = new HelperCommands(new PlaybackScope("helper-run", 7));
        var native = new PlaybackScope("native-owner", 2);
        foreach (var fixture in cases.EnumerateArray())
        {
            var args = fixture.GetProperty("args");
            ulong Number(string key, ulong fallback) => args.TryGetProperty(key, out var item) ? item.GetUInt64() : fallback;
            string Text(string key, string fallback) => args.TryGetProperty(key, out var item) ? item.GetString()! : fallback;
            bool Flag(string key) => args.TryGetProperty(key, out var item) && item.GetBoolean();
            ulong id = Number("id", 9); JsonNode? encoded = null;
            try
            {
                var actual = new PlaybackObservation { Scope = Flag("foreignNative") ? new PlaybackScope("foreign", 2) : native, Sequence = 1, Status = Flag("loading") ? PlaybackStatus.Loading : PlaybackStatus.Ready, PositionMs = 1300, DurationMs = Flag("loading") ? null : 4000, Paused = true, Rate = 1, Seekable = !Flag("loading"), Volume = .25, Muted = true };
                byte[] frame = fixture.GetProperty("op").GetString() switch
                {
                    "hello" => encoder.Hello(id),
                    "shutdown" => encoder.Shutdown(id),
                    "disconnect" => encoder.Disconnect(id),
                    "ready" => encoder.Ready(id, true),
                    "loaded" => encoder.Loaded(id, Text("handle", "media-1"), Text("name", "Кино 🎬.mkv"), (long)Number("durationMs", 4000), Number("fileSizeBytes", 4096)),
                    "connect" => encoder.Connect(id, Text("host", "127.0.0.1"), (int)Number("port", 8999), Text("room", "room"), Text("username", "Viewer"), accountAdmission: Flag("accountAdmission")),
                    "admit" => encoder.Admit(id, Text("nonce", new string('a', 32)), new(Text("ticket", new string('A', 64)), Text("clientRunId", "room-run"))),
                    "observation" => encoder.Observation(id, actual, native),
                    "user" => encoder.UserObservation(id, Number("sequence", 3), actual, native),
                    _ => throw new InvalidDataException("Unknown fixture operation")
                };
                if (frame[^1] != 10 || frame.Length > 65536) throw new InvalidDataException("Invalid outgoing framing");
                encoded = JsonNode.Parse(frame);
            }
            catch (Exception error) when (error is PlayerCorrectionException or ContractException) { }
            if (!JsonNode.DeepEquals(encoded, JsonNode.Parse(fixture.GetProperty("expected").GetRawText()))) throw new InvalidDataException(fixture.GetProperty("name").GetString());
        }
        Console.WriteLine("SYNCWATCH_HELPER_COMMANDS_OK 31 golden command and observation authority cases");
    }
}
