using System.Text;
using System.Text.Json;
using SyncWatch.Bridge;
using SyncWatch.Domain;

internal static class HelperReplyChecks
{
    internal static void Run(string path)
    {
        using var corpus = JsonDocument.Parse(File.ReadAllBytes(path));
        var cases = corpus.RootElement.GetProperty("cases");
        if (cases.GetArrayLength() != 40) throw new InvalidDataException("Incomplete reply corpus");
        var scope = new PlaybackScope("helper-run", 7);
        foreach (var fixture in cases.EnumerateArray())
        {
            var kind = Enum.Parse<HelperReplyKind>(fixture.GetProperty("expectedKind").GetString()!, true);
            bool accepted = false;
            try
            {
                var reply = HelperReply.Decode(Encoding.UTF8.GetBytes(fixture.GetProperty("frame").GetString()!), scope, 9, kind, fixture.GetProperty("expectedRevision").GetString(), requireAdmission: fixture.TryGetProperty("requireAdmission", out var required) && required.GetBoolean());
                accepted = true;
                if (fixture.GetProperty("name").GetString() == "rejected" && (reply.Kind is not null || reply.ErrorCode != "unavailable" || !reply.Retryable)) throw new InvalidDataException("Provider text became application state");
            }
            catch (Exception error) when (error is PlayerCorrectionException or ContractException or JsonException) { }
            if (accepted != fixture.GetProperty("accepted").GetBoolean()) throw new InvalidDataException(fixture.GetProperty("name").GetString());
        }
        Console.WriteLine("SYNCWATCH_HELPER_REPLIES_OK 40 correlated reply, scope and engine pin cases");
    }
}
