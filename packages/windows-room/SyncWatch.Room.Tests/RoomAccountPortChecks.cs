using System.Text;
using System.Text.Json;
using SyncWatch.Room;

internal static class RoomAccountPortChecks
{
    internal static async Task Run(JsonElement fixture)
    {
        using var bootstrap = JsonDocument.Parse(fixture.GetProperty("catalogBootstrap").GetString()!);
        var payload = new { roomId = "room-test", memberId = "member-owner", expiresAt = "2030-01-01T00:00:00Z", bootstrap = bootstrap.RootElement };
        byte[] bytes = RoomWire.Encode(payload); Guid scope = Guid.NewGuid();
        var admitted = RoomAccessWire.Admission(bytes, "run-room-client", scope);
        Require(admitted.Token is null && admitted.Invitation is null && admitted.IsAccountBacked && admitted.AccountScope == scope);
        Reject(() => RoomAccessWire.Admission(bytes, "other-run", scope));
        Reject(() => RoomAccessWire.Admission(bytes, "run-room-client", scope, "other-room"));
        Reject(() => RoomAccessWire.Admission(RoomWire.Encode(new { payload.roomId, payload.memberId, payload.expiresAt, payload.bootstrap, token = new string('A', 64) }), "run-room-client", scope));
        Reject(() => RoomAccessWire.Result(RoomWire.Encode(new { v = 1, result = payload })));
        Reject(() => RoomAccessWire.Result(Encoding.UTF8.GetBytes("{\"v\":2,\"v\":2,\"result\":{}}")));
        using var empty = RoomWire.Parse(RoomAccessWire.Result(RoomWire.Encode(new { v = 2, result = Array.Empty<object>() })));
        Require(empty.RootElement.GetArrayLength() == 0);

        var address = new RoomAddress("http://127.0.0.1:1/"); Guid owner = Guid.NewGuid();
        var device = new AccountDeviceSession(new(new string('A', 43), "Owner", "owner@example.invalid"), new string('B', 43), new string('R', 43), address.Origin, owner);
        using var authority = new RoomAccountAuthority(address, device, owner);
        await using var client = RoomClient.ForAccount(authority);
        try { _ = await client.SnapshotAsync(admitted); throw new Exception("Foreign authority reached transport"); }
        catch (RoomProtocolException error) when (error.Message == "malformed") { }
        var admission = RoomAccessWire.Admission(bytes, "run-room-client", authority.Scope);
        authority.Pause();
        Task<RoomAdmission> request = client.CreateForAccountAsync("run-new", "create-new");
        Task<RoomEventStream> events = client.OpenEventsAsync(admission);
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (authority.PendingRequests != 2) await Task.Delay(10, deadline.Token);
        await client.DisposeAsync();
        await Cancelled(request); await Cancelled(events);
        Require(authority.PendingRequests == 0);
        authority.Commit(device);
        Require((await authority.CredentialAsync()).AccountId == device.Account.Id);
        Console.WriteLine("SYNCWATCH_ROOM_ACCOUNT_PORT_OK");
    }
    private static async Task Cancelled<T>(Task<T> work)
    {
        try { _ = await work; throw new Exception("Closed client completed paused authorization"); }
        catch (OperationCanceledException) { }
        catch (RoomProtocolException error) when (error.Message == "closed") { }
    }
    private static void Reject(Action work)
    {
        try { work(); } catch (RoomProtocolException error) when (error.Message == "malformed") { return; }
        throw new Exception("Invalid account admission accepted");
    }
    private static void Require(bool condition) { if (!condition) throw new Exception("Account room port invariant"); }
}
