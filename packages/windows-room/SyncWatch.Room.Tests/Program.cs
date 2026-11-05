using System.Text;
using System.Text.Json;
using SyncWatch.Room;

AccountCredentialVaultChecks.Run();
await RoomAccountAuthorityChecks.Run();
AccountSessionChecks.Run(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(args[0]))!, "account-persistence.json"));

using var fixture = JsonDocument.Parse(File.ReadAllBytes(args[0]));
var identity = new RoomIdentity("run-room-client", "Room User");
var bootstrap = RoomBootstrap.Decode(Encoding.UTF8.GetBytes(fixture.RootElement.GetProperty("catalogBootstrap").GetString()!));
foreach (var entry in fixture.RootElement.GetProperty("catalogs").EnumerateArray())
{
    bool valid = entry.GetProperty("valid").GetBoolean(), accepted = false;
    RoomCatalogPage? page = null;
    try
    {
        page = RoomCatalogPage.Decode(Encoding.UTF8.GetBytes(entry.GetProperty("wire").GetString()!), bootstrap, entry.GetProperty("cursor").GetString()!);
        accepted = true;
    }
    catch (RoomProtocolException) { }
    if (valid != accepted) throw new Exception("catalog: " + entry.GetProperty("name").GetString());
    if (page is not null && (page.Epoch != bootstrap.Snapshot.Epoch || page.Sequence != bootstrap.Snapshot.Sequence
        || page.Items.Count != entry.GetProperty("count").GetInt32() || page.NextCursor != entry.GetProperty("nextCursor").GetString()))
        throw new Exception("catalog page facts");
}
foreach (var entry in fixture.RootElement.GetProperty("invitations").EnumerateArray())
{
    bool valid = entry.GetProperty("valid").GetBoolean(), accepted = false;
    RoomInvitation? invitation = null;
    try { invitation = RoomInvitation.Parse(entry.GetProperty("text").GetString()!); accepted = true; } catch (RoomProtocolException) { }
    if (accepted != valid) throw new Exception("invitation: " + entry.GetProperty("name").GetString());
    if (invitation is not null)
    {
        string roomId = entry.GetProperty("roomID").GetString()!, credential = entry.GetProperty("credential").GetString()!;
        if (invitation.RoomId != roomId || invitation.CopyText() != roomId + "\n" + credential || invitation.ToString().Contains(credential, StringComparison.Ordinal))
            throw new Exception("invitation normalization/redaction");
    }
}
foreach (var entry in fixture.RootElement.GetProperty("addresses").EnumerateArray())
{
    bool valid = entry.GetProperty("valid").GetBoolean(), accepted = false;
    try { _ = new RoomAddress(entry.GetProperty("value").GetString()!); accepted = true; } catch (Exception) { }
    if (valid != accepted) throw new Exception("address: " + entry.GetProperty("value").GetString());
}
foreach (var entry in fixture.RootElement.GetProperty("admissions").EnumerateArray())
{
    bool valid = entry.GetProperty("valid").GetBoolean(), accepted = false;
    RoomAdmission? admission = null;
    try { admission = RoomAdmission.Decode(Encoding.UTF8.GetBytes(entry.GetProperty("wire").GetString()!), identity, entry.GetProperty("owner").GetBoolean()); accepted = true; }
    catch (Exception) { }
    if (valid != accepted) throw new Exception("admission: " + entry.GetProperty("name").GetString());
    if (admission is not null && (admission.MemberId != "member-owner" || admission.Bootstrap.Connection.Port != 8999
        || admission.ToString().Contains(new string('A', 64), StringComparison.Ordinal) || admission.ToString().Contains(new string('B', 64), StringComparison.Ordinal))) throw new Exception("admission facts/redaction");
}
Console.WriteLine("SYNCWATCH_ROOM_PROTOCOL_OK");
SelectionChecks.Run(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(args[0]))!, "room-selection.json"));
await CatalogViewChecks.RunAsync(fixture.RootElement);
await CommandChecks.RunAsync(fixture.RootElement);
await EventStreamChecks.RunAsync(bootstrap);
await TransportChecks.RunAsync();
