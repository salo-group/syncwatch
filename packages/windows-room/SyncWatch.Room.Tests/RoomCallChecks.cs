using System.Text;
using System.Text.Json;
using SyncWatch.Room;

internal static class RoomCallChecks
{
    internal static void Run(string file)
    {
        using var document = JsonDocument.Parse(File.ReadAllBytes(file));
        foreach (var item in document.RootElement.GetProperty("statuses").EnumerateArray())
        {
            bool accepted;
            try { _ = RoomCallStatus.Decode(Encoding.UTF8.GetBytes(item.GetProperty("wire").GetString()!)); accepted = true; }
            catch (RoomProtocolException) { accepted = false; }
            if (accepted != item.GetProperty("valid").GetBoolean()) throw new Exception("Room call corpus: " + item.GetProperty("name").GetString());
        }
        Console.WriteLine("SYNCWATCH_ROOM_CALL_OK");
    }
}
