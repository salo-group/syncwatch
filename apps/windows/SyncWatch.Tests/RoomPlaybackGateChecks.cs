using SyncWatch;
using SyncWatch.Design;

internal static class RoomPlaybackGateChecks
{
    internal static async Task RunAsync()
    {
        var controller = new RoomController(legacyValidation: true);
        controller.Editor.SelectMode("joinRoom"); controller.Editor.Edit("serviceAddress", "https://rooms.example.com"); controller.Editor.Edit("username", "Peer");
        string credential = new('A', 64);
        controller.PasteInvitation("room-paste\r\n" + credential);
        if (controller.Editor.Values["roomID"] != "room-paste" || controller.Editor.Values["invitation"] != credential || !controller.Editor.IsValid || controller.Editor.Values["serviceAddress"] != "https://rooms.example.com")
            throw new InvalidOperationException("Invitation paste changed destination or failed to populate a valid draft");
        controller.Editor.State = "connecting"; controller.PasteInvitation("invalid");
        if (controller.Editor.Values["invitation"] != credential) throw new InvalidOperationException("Busy invitation draft changed");
        controller.Editor.State = "disconnected"; controller.PasteInvitation("invalid");
        if (controller.Editor.Values["invitation"] != "" || controller.Editor.Values["roomID"] != "" || controller.Editor.IsValid || controller.Editor.Status != "roomInvitationInvalid" || controller.Editor.Values["username"] != "Peer")
            throw new InvalidOperationException("Invalid invitation retained stale credentials or erased identity");
        var source = LibraryPresentation.Gallery(LibraryVisualState.Selected);
        if (!source.Allows("startWatching")) throw new InvalidOperationException("Missing local playback baseline");
        foreach (string state in new[] { "connecting", "connected", "failed" })
        {
            controller.Editor.State = state;
            if (controller.LibraryPresentation(source).Allows("startWatching")) throw new InvalidOperationException("Room attempt enabled unsynchronized playback");
            if (state == "failed" && controller.Editor.Context.Values["can.leaveRoom"] != "true") throw new InvalidOperationException("Failed room has no explicit exit");
            if (state == "failed" && controller.MediaNotice != "roomConnectionFailed") throw new InvalidOperationException("Room failure is hidden by playback presentation");
            await controller.CloseAsync();
            if (!controller.LibraryPresentation(source).Allows("startWatching")) throw new InvalidOperationException("Explicit exit did not restore local playback");
            if (controller.MediaNotice.Length != 0) throw new InvalidOperationException("Explicit exit retained a connection failure notice");
        }
        Console.WriteLine("SYNCWATCH_ROOM_PLAYBACK_GATE_OK explicit exit after failure");
        Console.WriteLine("SYNCWATCH_ROOM_INVITATION_OK atomic paste with an explicit service destination");
        await CompleteCatalogAsync();
    }

    private static async Task CompleteCatalogAsync()
    {
        string? address = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_SERVICE"), room = Environment.GetEnvironmentVariable("SYNCWATCH_CATALOG_ROOM"), invitation = Environment.GetEnvironmentVariable("SYNCWATCH_CATALOG_INVITATION");
        if (address is null || room is null || invitation is null) return;
        var controller = new RoomController(legacyValidation: true); controller.Editor.SelectMode("joinRoom");
        foreach (var (field, value) in new[] { ("serviceAddress", address), ("username", "Windows workspace catalog"), ("roomID", room), ("invitation", invitation) }) controller.Editor.Edit(field, value);
        try
        {
            controller.Connect();
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(20));
            while (controller.Editor.Busy) await Task.Delay(10, deadline.Token);
            var view = controller.CatalogView ?? throw new Exception("Missing complete controller catalog");
            if (!controller.Connected || view.Bootstrap != controller.Bootstrap || view.Items.Count != 250
                || !view.Items.Select(p => p.Media.MediaId).ToHashSet().SetEquals(Enumerable.Range(0, 250).Select(i => $"movie-{i}"))) throw new Exception("Incomplete controller catalog");
            await using var peer = new SyncWatch.Room.RoomClient(new(address));
            var joined = await peer.JoinAsync(room, new(invitation), new("run-" + Guid.NewGuid().ToString("N"), "Windows catalog refresh"));
            var events = await peer.OpenEventsAsync(joined);
            _ = SyncWatch.Room.RoomBootstrap.InitialEvent(await events.ReceiveAsync(), joined);
            using var updates = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            while (controller.Bootstrap?.Snapshot.Members.Any(p => p.MemberId == joined.MemberId) != true)
            {
                if (controller.CatalogView?.Items.Count != 250) throw new Exception("Partial catalog during join refresh");
                await Task.Delay(10, updates.Token);
            }
            if (controller.CatalogView?.Items.Count != 250 || (controller.Bootstrap ?? throw new Exception("Lost refreshed bootstrap")).Snapshot.Sequence <= view.Bootstrap.Snapshot.Sequence) throw new Exception("Catalog refresh did not advance atomically");
            await peer.LeaveAsync(joined);
            while (controller.Bootstrap?.Snapshot.Members.Any(p => p.MemberId == joined.MemberId) == true)
            {
                if (controller.CatalogView?.Items.Count != 250) throw new Exception("Partial catalog during leave refresh");
                await Task.Delay(10, updates.Token);
            }
            await controller.CloseAsync();
            if (controller.CatalogView is not null || controller.Bootstrap is not null || view.Items.Count != 250) throw new Exception("Controller catalog lifetime");
            await controller.CloseAsync();
            Console.WriteLine("SYNCWATCH_ROOM_CONTROLLER_CATALOG_OK native 250-item view/join refresh/leave refresh/close/repeated close");
        }
        finally { await controller.CloseAsync(); }
    }
}
