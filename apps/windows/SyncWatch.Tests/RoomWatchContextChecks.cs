using SyncWatch;
using SyncWatch.Domain;
using SyncWatch.Room;

internal static class RoomWatchContextChecks
{
    internal static async Task RunAsync()
    {
        if (RoomWatchCoordinator.FailureKey(new SharedMediaUnavailableException(), false) != "sharedWatchMediaMissing"
            || RoomWatchCoordinator.FailureKey(new SharedMediaUnavailableException(), true) != "sharedWatchIdentityRejected") throw new Exception("Missing media failure classification");
        if (!RoomController.IsMetadataInterruption(new System.Net.Http.HttpRequestException()) || !RoomController.IsMetadataInterruption(new RoomProtocolException("transport"))) throw new Exception("Transport interruption classification");
        if (!RoomController.IsMetadataInterruption(new System.Net.WebSockets.WebSocketException())) throw new Exception("Event handshake interruption classification");
        foreach (string code in new[] { "malformed", "unauthorized", "forbidden", "frameTooLarge" })
            if (RoomController.IsMetadataInterruption(new RoomProtocolException(code))) throw new Exception("Invalid membership retained");
        if (RoomWatchCoordinator.FailureKey(new RoomProtocolException("staleGeneration"), false) != "sharedWatchRoomChanged"
            || RoomWatchCoordinator.FailureKey(new RoomProtocolException("staleGeneration"), true) != "sharedWatchIdentityRejected") throw new Exception("Shared intent failure lost actionable classification");
        foreach (Exception error in new Exception[] { new RoomProtocolException("unauthorized"), new System.Net.Http.HttpRequestException(), new Exception("C:/private/movie/token") })
            if (RoomWatchCoordinator.FailureKey(error, false) != "sharedWatchFailed") throw new Exception("Shared failure exposed unclassified details");
        string? address = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_SERVICE"), key = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_CREATION_KEY");
        if (address is null || key is null) return;
        await RoomEventMonitorChecks.RunAsync(address, key);
        await PreparedPublicationAsync(address, key);
        await using var client = new RoomClient(new(address)); await using var peer = new RoomClient(new(address));
        var identity = new RoomIdentity("run-" + Guid.NewGuid().ToString("N"), "Windows watch");
        var admission = await client.CreateAsync(identity, new("syncplay.example.com", 8999, "Watch context"), new(key));
        var stream = await client.OpenEventsAsync(admission);
        _ = RoomBootstrap.InitialEvent(await stream.ReceiveAsync(), admission);
        var owner = new RoomMembershipSession(client, admission, identity);
        var descriptor = new MediaDescriptor("movie-a", "Movie", "movie", 1000, 60000, new string('a', 64));
        _ = await owner.PublishAsync([descriptor]);
        var idle = await owner.WatchContextAsync();
        if (idle.Definition.Selection is not null) throw new Exception("Initial watch selection");
        var selected = await owner.PerformAsync(new RoomCommandAction.Select("movie-a"), idle);
        if (selected.Definition.Selection?.Media != descriptor) throw new Exception("Selected edition");
        _ = await owner.PublishAsync([]);
        if ((await owner.WatchContextAsync()).Definition.Selection?.Media != descriptor) throw new Exception("Mutable catalog redefined selection");
        var peerIdentity = new RoomIdentity("run-" + Guid.NewGuid().ToString("N"), "Windows peer");
        var joined = await peer.JoinAsync(admission.RoomId, admission.Invitation!, peerIdentity);
        var peerStream = await peer.OpenEventsAsync(joined);
        _ = RoomBootstrap.InitialEvent(await peerStream.ReceiveAsync(), joined);
        var peerOwner = new RoomMembershipSession(peer, joined, peerIdentity);
        _ = await peerOwner.PublishAsync([descriptor]);
        _ = await peerOwner.PerformAsync(new RoomCommandAction.Select("movie-a"), await peerOwner.WatchContextAsync());
        bool stale = false;
        try { _ = await owner.PerformAsync(new RoomCommandAction.Start("movie-a"), selected); }
        catch (RoomProtocolException error) when (error.Message == "staleGeneration") { stale = true; }
        if (!stale) throw new Exception("Stale user intent overwrote peer generation");
        var active = await owner.PerformAsync(new RoomCommandAction.Start("movie-a"), await owner.WatchContextAsync());
        if (active.Bootstrap.Snapshot.WatchStatus != "active" || active.Definition.Selection?.MemberId != joined.MemberId) throw new Exception("Start definition");
        var renamed = new MediaDescriptor("private-local-id", "Renamed", "renamed", 1000, 60000, new string('a', 64));
        RoomWatchCoordinator.RequireMatch(renamed, active);
        void Reject(Action action)
        {
            bool rejected = false; try { action(); } catch (Exception) { rejected = true; }
            if (!rejected) throw new Exception("Invalid shared admission accepted");
        }
        Reject(() => RoomWatchCoordinator.RequireMatch(renamed, selected));
        Reject(() => RoomWatchCoordinator.RequireSameGeneration(selected, active));
        foreach (var changed in new[] {
            new MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60000, new string('b', 64)),
            new MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60001, new string('a', 64)),
            new MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60000, null) })
            Reject(() => RoomWatchCoordinator.RequireMatch(changed, active));
        var finished = await owner.PerformAsync(new RoomCommandAction.End(RoomWatchEndReason.Finished), active);
        if (!RoomWatchCoordinator.CompletedPlaylistAvailable(active.Bootstrap.Snapshot, active.Bootstrap.Snapshot) ||
            !RoomWatchCoordinator.CompletedPlaylistAvailable(active.Bootstrap.Snapshot, finished.Bootstrap.Snapshot)) throw new Exception("Matching native completion lost playlist authority");
        var next = await owner.PerformAsync(new RoomCommandAction.Select("movie-a"), finished);
        if (RoomWatchCoordinator.CompletedPlaylistAvailable(active.Bootstrap.Snapshot, next.Bootstrap.Snapshot)) throw new Exception("Stale completion authorized a selection");
        var restarted = await owner.PerformAsync(new RoomCommandAction.Start("movie-a"), next);
        if (RoomWatchCoordinator.CompletedPlaylistAvailable(active.Bootstrap.Snapshot, restarted.Bootstrap.Snapshot)) throw new Exception("Stale completion authorized a later load");
        var laterEnd = await owner.PerformAsync(new RoomCommandAction.End(RoomWatchEndReason.Finished), restarted);
        if (RoomWatchCoordinator.CompletedPlaylistAvailable(active.Bootstrap.Snapshot, laterEnd.Bootstrap.Snapshot) ||
            !RoomWatchCoordinator.CompletedPlaylistAvailable(restarted.Bootstrap.Snapshot, laterEnd.Bootstrap.Snapshot)) throw new Exception("Completion escaped its original generation");
        await peer.LeaveAsync(joined); await client.LeaveAsync(admission);
        Console.WriteLine("SYNCWATCH_ROOM_WATCH_CONTEXT_OK");
    }
    private static async Task PreparedPublicationAsync(string address, string key)
    {
        var controller = new RoomController(legacyValidation: true);
        foreach (var (field, value) in new[] { ("serviceAddress", address), ("username", "Windows prepared publisher"), ("creationKey", key), ("syncplayHost", "syncplay.example.com"), ("roomName", "Prepared publication lifecycle") }) controller.Editor.Edit(field, value);
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(20));
        try
        {
            controller.Connect(); while (!controller.Connected) { if (controller.Editor.State == "failed") throw new Exception("Connect failed"); await Task.Delay(20, deadline.Token); }
            var intent = await controller.BeginWatchAsync(deadline.Token);
            var media = new MediaDescriptor("private-loaded-file", "Loaded", "loaded", 1000, 60000, new string('a', 64));
            var first = await controller.PublishPreparedAsync(media, intent.Membership, deadline.Token);
            if (first.Media.MediaId == media.MediaId || first.Media.FullHashSha256 != media.FullHashSha256) throw new Exception("Strong publication lost opaque identity or hash");
            if (!controller.CanContinuePlayback(intent.Membership)) throw new Exception("Current playback membership unavailable");
            var context = await controller.PerformWatchAsync(new RoomCommandAction.Select(first.Media.MediaId), intent.Membership, intent.Context, deadline.Token);
            context = await controller.PerformWatchAsync(new RoomCommandAction.Start(first.Media.MediaId), intent.Membership, context, deadline.Token);
            var empty = new RoomController(legacyValidation: true);
            try
            {
                empty.Editor.SelectMode("joinRoom"); empty.Editor.Edit("serviceAddress", address); empty.Editor.Edit("username", "Windows empty library");
                empty.PasteInvitation(controller.InvitationToCopy() ?? throw new Exception("Invitation missing")); empty.Connect();
                while (empty.MediaNotice != "sharedWatchMediaMissing" || controller.MediaNotice != "roomPeersMissingMedia") await Task.Delay(20, deadline.Token);
                await empty.CloseAsync();
                while (controller.MediaNotice.Length != 0) await Task.Delay(20, deadline.Token);
            }
            finally { await empty.CloseAsync(); }
            _ = await controller.PerformWatchAsync(new RoomCommandAction.End(RoomWatchEndReason.User), intent.Membership, context, deadline.Token);
            if (!controller.CanContinuePlayback(intent.Membership)) throw new Exception("Bookkeeping revoked peer playback");
            var second = await controller.PublishPreparedAsync(media, intent.Membership, deadline.Token);
            if (second.Media != first.Media || second.Lease == first.Lease) throw new Exception("Publication lease identity");
            controller.ClearPrepared(first.Lease, intent.Membership);
            if (controller.CatalogView?.Items.Any(p => p.Media == second.Media) != true) throw new Exception("Stale cleanup removed newer lease");
            controller.ClearPrepared(second.Lease, intent.Membership);
            while (controller.CatalogView?.Items.Count != 0) await Task.Delay(20, deadline.Token);
            await controller.CloseAsync();
            if (controller.IsCurrent(intent.Membership) || controller.CanContinuePlayback(intent.Membership) || controller.WatchBootstrap is not null) throw new Exception("Membership retained authority");
            bool rejected = false; try { await controller.PublishPreparedAsync(media, intent.Membership, deadline.Token); } catch (RoomProtocolException) { rejected = true; }
            if (!rejected) throw new Exception("Stale membership published");
            Console.WriteLine("SYNCWATCH_PREPARED_PUBLICATION_OK opaque strong ACK/new lease/stale cleanup/leave/repeated close");
        }
        finally { await controller.CloseAsync(); }
    }
}
