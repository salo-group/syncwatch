using System.Text;
using System.Text.Json;
using SyncWatch.Room;

internal static class RoomMeetAuthorizationChecks
{
    internal static void Run(string file)
    {
        using var document = JsonDocument.Parse(File.ReadAllBytes(file));
        string account = document.RootElement.GetProperty("accountId").GetString()!;
        foreach (var item in document.RootElement.GetProperty("statuses").EnumerateArray())
        {
            bool accepted;
            try { _ = RoomMeetAuthorizationStatus.Decode(Encoding.UTF8.GetBytes(item.GetProperty("wire").GetString()!), account); accepted = true; }
            catch (RoomProtocolException) { accepted = false; }
            if (accepted != item.GetProperty("valid").GetBoolean()) throw new Exception("Meet authorization corpus: " + item.GetProperty("name").GetString());
        }
        Console.WriteLine("SYNCWATCH_MEET_AUTHORIZATION_OK");
    }
}
