using SyncWatch.Room;

internal static class RoomMeetTransportChecks
{
    internal static async Task<RoomMeetAuthorization> Run(RoomClient host, RoomClient peer, RoomAdmission owner,
        RoomAdmission guest, AccountProfile account, string fixtureToken)
    {
        async Task<string?> Browser(RoomMeetAuthorization flow, string scenario)
        {
            byte[] body = RoomWire.Encode(new { v = 2, transactionId = flow.Id, scenario, fixtureToken });
            using var document = RoomWire.Parse(await host.RequestAsync(HttpMethod.Post, "__test/meet-browser", null, body, default));
            var value = document.RootElement; RoomWire.Keys(value, "browserProof");
            return RoomWire.OptionalString(value.GetProperty("browserProof"));
        }
        try { _ = await peer.BeginMeetAuthorizationAsync(guest); throw new Exception("Guest requested organizer permission"); }
        catch (RoomProtocolException error) when (error.Message == "forbidden") { }
        var call = host.CallStartOperation(owner);
        var unsignedCall = await host.RequestCallAsync(call, owner);
        Require(unsignedCall.State == "idle" && unsignedCall.AuthorizationRequired);
        var mismatch = await host.BeginMeetAuthorizationAsync(owner);
        Require((await host.MeetAuthorizationStatusAsync(mismatch, owner)).State == "pending");
        Require(await Browser(mismatch, "wrongAccount") is null);
        var failed = await host.MeetAuthorizationStatusAsync(mismatch, owner);
        Require(failed.State == "failed" && failed.Reason == "accountMismatch");
        var refused = await host.BeginMeetAuthorizationAsync(owner);
        Require(await Browser(refused, "missingScope") is null);
        Require((await host.MeetAuthorizationStatusAsync(refused, owner)).Reason == "permissionUnavailable");
        var flow = await host.BeginMeetAuthorizationAsync(owner);
        Require(!flow.ToString().Contains(flow.Proof, StringComparison.Ordinal));
        try { _ = await peer.MeetAuthorizationStatusAsync(flow, guest); throw new Exception("Foreign flow reached transport"); }
        catch (RoomProtocolException error) when (error.Message == "malformed") { }
        string code = await Browser(flow, "accept") ?? throw new Exception("Synthetic browser confirmation missing");
        var waiting = await host.MeetAuthorizationStatusAsync(flow, owner);
        Require(waiting.State == "browserConfirmationRequired" && waiting.Account is null);
        string wrong = (code[0] == 'A' ? "B" : "A") + code[1..];
        var invalid = await host.VerifyMeetAuthorizationAsync(flow, owner, wrong);
        Require(invalid.Reason == "invalidConfirmation" && invalid.Account is null);
        var verified = await host.VerifyMeetAuthorizationAsync(flow, owner, code);
        Require(verified.State == "confirmAuthorization" && verified.Account == account);
        for (int attempt = 0; attempt < 2; attempt++) Require((await host.ConfirmMeetAuthorizationAsync(flow, owner, code)).State == "complete");
        Require((await host.CancelMeetAuthorizationAsync(flow, owner)).State == "complete");
        _ = await host.RequestCallAsync(call, owner);
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var ready = await host.CallStatusAsync(owner);
        while (ready.State == "creating") { await Task.Delay(10, deadline.Token); ready = await host.CallStatusAsync(owner, deadline.Token); }
        Require(ready.State == "ready" && ready.MeetUri is not null);
        var retry = await host.RequestCallAsync(call, owner);
        Require(retry.CallId == ready.CallId && retry.Generation == ready.Generation);
        var guestCall = await peer.CallStatusAsync(guest);
        Require(guestCall.MeetUri == ready.MeetUri && !guestCall.AuthorizationRequired);
        var forbiddenEnd = peer.CallEndOperation(guest, guestCall);
        try { _ = await peer.RequestCallAsync(forbiddenEnd, guest); throw new Exception("Guest ended owner call"); }
        catch (RoomProtocolException error) when (error.Message == "forbidden") { }
        var end = host.CallEndOperation(owner, ready);
        _ = await host.RequestCallAsync(end, owner);
        var ended = await host.CallStatusAsync(owner);
        while (ended.State == "ending") { await Task.Delay(10, deadline.Token); ended = await host.CallStatusAsync(owner, deadline.Token); }
        Require(ended.State == "ended" && ended.MeetUri is null);
        Require((await host.RequestCallAsync(end, owner)).State == "ended");
        Console.WriteLine("SYNCWATCH_ROOM_CALL_TRANSPORT_OK synthetic provider, current membership, stable create/end, guest denial and URI fencing");
        var reuse = await host.BeginMeetAuthorizationAsync(owner);
        Require(await Browser(reuse, "reuse") is not null);
        for (int attempt = 0; attempt < 2; attempt++) Require((await host.CancelMeetAuthorizationAsync(reuse, owner)).State == "cancelled");
        Console.WriteLine("SYNCWATCH_MEET_AUTHORIZATION_TRANSPORT_OK synthetic provider, OAuth scope/PKCE, counterproof, owner scope, retry and cancellation");
        return await host.BeginMeetAuthorizationAsync(owner);
    }
    private static void Require(bool value) { if (!value) throw new Exception("Meet authorization transport invariant"); }
}
