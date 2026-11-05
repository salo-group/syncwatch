using System.Text.Json;
using SyncWatch.Room;

internal static class RoomAccountTransportChecks
{
    internal static async Task Run()
    {
        string? path = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_ACCOUNT_FIXTURE");
        if (path is null) { Console.WriteLine("SKIP owned authenticated room service: SYNCWATCH_ROOM_ACCOUNT_FIXTURE is not set"); return; }
        using var fixture = RoomWire.Parse(await File.ReadAllBytesAsync(path));
        var ownerRecord = AccountSavedSession.Decode(RoomWire.Encode(fixture.RootElement.GetProperty("owner")));
        var guestRecord = AccountSavedSession.Decode(RoomWire.Encode(fixture.RootElement.GetProperty("guest")));
        using var ownerAccount = new AccountClient(new(ownerRecord.Service));
        using var guestAccount = new AccountClient(new(guestRecord.Service));
        var ownerDevice = await ownerAccount.ResumeAsync(ownerRecord);
        var guestDevice = await guestAccount.ResumeAsync(guestRecord);
        using var owner = ownerAccount.RoomAuthority(ownerDevice); using var guest = guestAccount.RoomAuthority(guestDevice);
        await using var host = RoomClient.ForAccount(owner); await using var peer = RoomClient.ForAccount(guest);
        var admission = await host.CreateForAccountAsync("windows-" + Guid.NewGuid().ToString("N"), "create-" + Guid.NewGuid().ToString("N"));
        Require(admission.IsAccountBacked);
        var hostEvents = await host.OpenEventsAsync(admission);
        _ = RoomBootstrap.InitialEvent(await hostEvents.ReceiveAsync(), admission);
        var invitation = await host.InviteAsync(admission);
        var attempt = await peer.RequestAdmissionAsync(invitation, "guest-" + Guid.NewGuid().ToString("N"), "join-" + Guid.NewGuid().ToString("N"));
        Require(attempt.Request.Account == guestDevice.Account);
        Require((await host.PendingAsync(admission)).Single().Id == attempt.Request.Id);
        try { _ = await peer.AcceptAsync(attempt); throw new Exception("Unapproved device admitted"); }
        catch (RoomProtocolException error) when (error.Message == "admissionRequired") { }
        await host.DecideAsync(admission, attempt.Request.Id, true);
        Require((await peer.AdmissionStatusAsync(attempt)).Status == "approved");
        var joined = await peer.AcceptAsync(attempt);
        var events = await peer.OpenEventsAsync(joined);
        Require(RoomBootstrap.InitialEvent(await events.ReceiveAsync(), joined).Snapshot.Members.Count == 2);
        using var bound = RoomWire.Parse(await hostEvents.ReceiveAsync());
        Require(bound.RootElement.GetProperty("type").GetString() == "member.bound");
        var current = await host.SnapshotAsync(admission); Require(current.Snapshot.Members.Count == 2);
        Require((await host.CatalogAsync(admission, current, current.CatalogCursor)).Items.Count == 0);
        var first = await peer.SyncTicketAsync(joined); var second = await peer.SyncTicketAsync(joined);
        Require(first.Credential.Value != second.Credential.Value);
        var withdrawal = await peer.RequestAdmissionAsync(await host.InviteAsync(admission), "withdraw-" + Guid.NewGuid().ToString("N"), "withdraw-" + Guid.NewGuid().ToString("N"));
        await peer.CancelAdmissionAsync(withdrawal); await peer.CancelAdmissionAsync(withdrawal);
        Require((await peer.AdmissionStatusAsync(withdrawal)).Status == "revoked");
        var race = await peer.RequestAdmissionAsync(await host.InviteAsync(admission), "race-" + Guid.NewGuid().ToString("N"), "race-" + Guid.NewGuid().ToString("N"));
        await host.DecideAsync(admission, race.Request.Id, true);
        _ = await peer.AcceptAsync(race);
        await peer.CancelAdmissionAsync(race);
        Require((await host.SnapshotAsync(admission)).Snapshot.Members.Count == 2);
        Require((await peer.AdmissionStatusAsync(race)).Status == "revoked");
        owner.Pause();
        var rotated = await ownerAccount.ResumeAsync(ownerAccount.Checkpoint(ownerDevice));
        _ = ownerAccount.Checkpoint(rotated); owner.Commit(rotated);
        _ = await host.SnapshotAsync(admission);
        await guestAccount.LogoutAsync(guestDevice);
        try { _ = await peer.SnapshotAsync(joined); throw new Exception("Logged-out device retained access"); }
        catch (RoomProtocolException error) when (error.Message == "unauthorized") { }
        await host.LeaveAsync(admission); await ownerAccount.LogoutAsync(rotated);
        Console.WriteLine("SYNCWATCH_AUTHENTICATED_ROOM_TRANSPORT_OK");
    }
    private static void Require(bool condition) { if (!condition) throw new Exception("Authenticated room transport invariant"); }
}
