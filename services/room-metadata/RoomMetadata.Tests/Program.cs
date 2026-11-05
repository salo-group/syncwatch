using System.Net;
using System.Net.Http.Headers;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using SyncWatch.RoomMetadata;

int assertions = 0;
List<object> observed = [];
void Check(bool value, string message)
{
    assertions++;
    if (!value) { throw new InvalidOperationException(message); }
}
void Reject(Action action, string code)
{
    try { action(); }
    catch (RoomException error) { Check(error.Code == code, $"Expected {code}, got {error.Code}"); return; }
    throw new InvalidOperationException($"Expected {code}");
}
JsonElement Json(object value) => Wire.Parse(JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json));
JsonElement CreateBody(string run = "run-owner", string user = "owner") => Json(new
{
    v = 1,
    clientRunId = run,
    syncplayUsername = user,
    syncplayHost = "syncplay.example.invalid",
    syncplayPort = 8999,
    syncplayRoom = "movie-room"
});
JsonElement JoinBody(Admission owner, string run = "run-guest", string user = "guest") => Json(new
{
    v = 1,
    invitation = owner.Invitation,
    clientRunId = run,
    syncplayUsername = user
});
object Media(string id = "movie-a", string title = "  Movie   A  ", string key = "movie a") => new
{
    v = 1,
    mediaId = id,
    title,
    titleKey = key,
    fileSizeBytes = 1024,
    durationMs = 10000
};
Command Cmd(Admission member, long number, string type, object payload, long? generation = null, string? epoch = null, string? run = null) => Wire.ReadCommand(JsonSerializer.SerializeToUtf8Bytes(new
{
    v = 1,
    id = "cmd-" + number,
    type,
    clientRunId = run ?? (member.Bootstrap.Snapshot.Members.Single(item => item.MemberId == member.MemberId).ClientRunId),
    roomEpoch = epoch ?? member.Bootstrap.Snapshot.RoomEpoch,
    watchGeneration = generation ?? 0,
    payload
}, Wire.Json));
Command Catalog(Admission owner, long number, object[] items, string batch = "batch-1", int offset = 0, bool final = true) =>
    Cmd(owner, number, "catalog.updated", new { batchId = batch, offset, items, final });

// Auth scopes, atomic catalogs, compare-and-set selection, replay and snapshot consistency.
RoomStore core = new(new RoomLimits { CommandsPerSecond = 256 });
Admission owner = core.Create(CreateBody());
Admission otherRoom = core.Create(CreateBody("run-other", "other"));
Admission guest = core.Join(owner.RoomId, JoinBody(owner));
Reject(() => core.Snapshot(otherRoom.RoomId, owner.Token), "unauthorized");
Reject(() => core.Join(owner.RoomId, JoinBody(owner, "run-duplicate", "guest")), "conflict");
Reject(() => core.RotateInvitation(owner.RoomId, guest.Token), "forbidden");
using RoomStream ownerStream = core.Attach(owner.RoomId, owner.Token, null, null);
using RoomStream guestStream = core.Attach(owner.RoomId, guest.Token, null, null);
byte[] bootstrap = await ownerStream.Frames.Reader.ReadAsync();
observed.Add(new { schema = "room-snapshot", value = JsonDocument.Parse(bootstrap).RootElement.GetProperty("data").GetProperty("snapshot").Clone() });
_ = await guestStream.Frames.Reader.ReadAsync();
Check(core.Execute(owner.RoomId, owner.Token, Catalog(owner, 1, [Media()], final: false)).Error is null, "First catalog stage");
Bootstrap beforeCommit = core.Snapshot(owner.RoomId, owner.Token);
Check(core.Catalog(owner.RoomId, owner.Token, beforeCommit.CatalogCursor).Items.Length == 0, "Partial catalogs must not leak into snapshots");
Command finalBatch = Catalog(owner, 2, [Media("movie-b", "Cafe\u0301", "caf\u00e9")], offset: 1);
Check(core.Execute(owner.RoomId, owner.Token, finalBatch).Error is null, "Commit catalog");
CommandResult duplicate = core.Execute(owner.RoomId, owner.Token, finalBatch);
Check(core.Execute(owner.RoomId, owner.Token, finalBatch) == duplicate, "Identical retries reuse their result");
Reject(() => core.Execute(owner.RoomId, owner.Token, Catalog(owner, 2, [])), "conflict");
Bootstrap committed = core.Snapshot(owner.RoomId, owner.Token);
Check(core.Catalog(owner.RoomId, owner.Token, committed.CatalogCursor).Items.Length == 2, "Committed catalog contents");
Command select = Cmd(owner, 3, "selection.changed", new { mediaId = "movie-a" });
Command guestSelect = Cmd(guest, 1, "selection.changed", new { mediaId = "movie-b" });
CommandResult[] race = await Task.WhenAll(Task.Run(() => core.Execute(owner.RoomId, owner.Token, select)), Task.Run(() => core.Execute(owner.RoomId, guest.Token, guestSelect)));
Check(race.Count(item => item.Error is null) == 1 && race.Count(item => item.Error == "staleGeneration") == 1, "Conflicting selections must have one winner");
Bootstrap selected = core.Snapshot(owner.RoomId, owner.Token);
Check(selected.Snapshot.WatchGeneration == 1 && selected.Snapshot.Watch.Status == "selected", "Selection allocates one watch generation");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 4, "playback.seek", new { positionMs = 1 }, 1)).Error == "malformed", "Metadata must never route playback");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 5, "call.linkChanged", new { meetUri = "https://meet.google.com/aaa-bbbb-ccc" }, 1, run: "foreign")).Error == "foreignRun", "Run binding");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 6, "call.linkChanged", new { meetUri = "https://meet.google.com/aaa-bbbb-ccc" }, 1, epoch: "old-epoch")).Error == "staleEpoch", "Epoch binding");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 7, "watch.started", new { mediaId = selected.Snapshot.Watch.MediaId }, 1)).Error is null, "Start bookkeeping");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 8, "watch.ended", new { reason = "user" }, 2)).Error is null, "End bookkeeping");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 9, "call.linkChanged", new { meetUri = "https://meet.google.com/aaa-bbbb-ccc" }, 3)).Error is null, "Meet URI metadata");
Bootstrap stable = core.Snapshot(owner.RoomId, owner.Token);
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 10, "catalog.updated", new { batchId = "clear", offset = 0, items = Array.Empty<object>(), final = true }, 3)).Error is null, "Clear catalog");
Check(core.Catalog(owner.RoomId, owner.Token, stable.CatalogCursor).Items.Length == 2, "A catalog cursor is a stable snapshot, not a moving list");
Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, 11, "media.missing", new { mediaId = selected.Snapshot.Watch.MediaId, missing = true }, 3)).Error is null, "Missing media metadata");
Check(core.Snapshot(owner.RoomId, owner.Token).MissingMembers.SequenceEqual([owner.MemberId]), "Snapshots retain missing-media membership");
long unchanged = core.Snapshot(owner.RoomId, owner.Token).Snapshot.Sequence;
int commandNumber = 12;
foreach (string forbidden in new[] { "player.setPaused", "player.setSpeed", "settings.changed", "camera.setEnabled" })
{
    Check(core.Execute(owner.RoomId, owner.Token, Cmd(owner, commandNumber++, forbidden, new { value = 1 }, 3)).Error == "malformed", "Only metadata commands are accepted");
}
Check(core.Snapshot(owner.RoomId, owner.Token).Snapshot.Sequence == unchanged, "Rejected settings/playback commands cannot emit events");
List<RoomEvent> events = [];
while (ownerStream.Frames.Reader.TryRead(out byte[]? frame))
{
    RoomEvent value = JsonSerializer.Deserialize<RoomEvent>(frame, Wire.Json)!;
    observed.Add(new { schema = "room-event", value = JsonDocument.Parse(frame).RootElement.Clone() });
    events.Add(value);
}
Check(events.Zip(events.Skip(1)).All(pair => pair.Second.Sequence == pair.First.Sequence + 1), "Strict event ordering");
long lastCursor = events[^2].Sequence;
core.Detach(owner.RoomId, ownerStream);
using RoomStream reconnected = core.Attach(owner.RoomId, owner.Token, stable.Snapshot.RoomEpoch, lastCursor);
RoomEvent replay = JsonSerializer.Deserialize<RoomEvent>(await reconnected.Frames.Reader.ReadAsync(), Wire.Json)!;
Check(replay.Sequence == lastCursor + 1, "Resume must replay exactly the missing event");
Check(!reconnected.Frames.Reader.TryRead(out _), "No duplicate replay");
Reject(() => core.Attach(owner.RoomId, owner.Token, null, null), "conflict");
core.Leave(owner.RoomId, owner.Token, guest.MemberId);
observed.Add(new { schema = "room-event", value = JsonDocument.Parse(await reconnected.Frames.Reader.ReadAsync()).RootElement.Clone() });
Check(guestStream.Stopped.IsCancellationRequested, "Revocation stops active streams");
Reject(() => core.Snapshot(owner.RoomId, guest.Token), "unauthorized");
JsonElement invitationGrant = Json(core.RotateInvitation(owner.RoomId, owner.Token));
Reject(() => core.Join(owner.RoomId, JoinBody(owner, "run-revoked", "revoked")), "unauthorized");
core.Join(owner.RoomId, Json(new { v = 1, invitation = invitationGrant.GetProperty("invitation").GetString(), clientRunId = "run-new", syncplayUsername = "new-guest" }));
observed.Add(new { schema = "room-event", value = JsonDocument.Parse(await reconnected.Frames.Reader.ReadAsync()).RootElement.Clone() });

// Retained command IDs cannot be replayed after eviction, and per-member input is bounded.
RoomStore dedup = new(new RoomLimits { CommandsPerSecond = 256 });
Admission writer = dedup.Create(CreateBody());
for (int number = 1; number <= 130; number++)
{
    Check(dedup.Execute(writer.RoomId, writer.Token, Cmd(writer, number, "call.linkChanged", new { meetUri = (string?)null })).Error is null, "Replay cache fill");
}
Reject(() => dedup.Execute(writer.RoomId, writer.Token, Cmd(writer, 1, "call.linkChanged", new { meetUri = (string?)null })), "duplicate");
RoomStore bounded = new(new RoomLimits { CommandsPerSecond = 2, MaximumMembers = 1, MaximumRooms = 1 });
Admission limited = bounded.Create(CreateBody());
Reject(() => bounded.Create(CreateBody()), "queueFull");
Reject(() => bounded.Join(limited.RoomId, JoinBody(limited)), "queueFull");
bounded.Execute(limited.RoomId, limited.Token, Cmd(limited, 1, "call.linkChanged", new { meetUri = (string?)null }));
bounded.Execute(limited.RoomId, limited.Token, Cmd(limited, 2, "call.linkChanged", new { meetUri = (string?)null }));
Reject(() => bounded.Execute(limited.RoomId, limited.Token, Cmd(limited, 3, "call.linkChanged", new { meetUri = (string?)null })), "queueFull");

// Deterministic expiration and disconnect cleanup; no sleeps or wall-clock assertions.
ManualClock clock = new();
RoomStore timed = new(new RoomLimits { InvitationLifetime = TimeSpan.FromSeconds(2), DisconnectGrace = TimeSpan.FromSeconds(5), RoomLifetime = TimeSpan.FromSeconds(30), CatalogSnapshotLifetime = TimeSpan.FromSeconds(2), ReplayEvents = 2 }, clock);
Admission timedOwner = timed.Create(CreateBody());
using RoomStream timedStream = timed.Attach(timedOwner.RoomId, timedOwner.Token, null, null);
Admission timedGuest = timed.Join(timedOwner.RoomId, JoinBody(timedOwner));
clock.Advance(TimeSpan.FromSeconds(3));
Reject(() => timed.Catalog(timedOwner.RoomId, timedOwner.Token, timedOwner.Bootstrap.CatalogCursor), "snapshotRequired");
Reject(() => timed.Join(timedOwner.RoomId, JoinBody(timedOwner, "late", "late")), "unauthorized");
clock.Advance(TimeSpan.FromSeconds(3)); timed.Sweep();
Reject(() => timed.Snapshot(timedOwner.RoomId, timedGuest.Token), "unauthorized");
Check(timed.Snapshot(timedOwner.RoomId, timedOwner.Token).Snapshot.Members.Length == 1, "Disconnected memberships and catalogs are cleaned up");
timed.Detach(timedOwner.RoomId, timedStream);
Reject(() => timed.Attach(timedOwner.RoomId, timedOwner.Token, timedOwner.Bootstrap.Snapshot.RoomEpoch, 0), "snapshotRequired");
using RoomStream resumed = timed.Attach(timedOwner.RoomId, timedOwner.Token, null, null);
clock.Advance(TimeSpan.FromSeconds(30)); timed.Sweep();
Check(resumed.Stopped.IsCancellationRequested, "Room expiry must stop capture-independent metadata streams");
Reject(() => timed.Snapshot(timedOwner.RoomId, timedOwner.Token), "unavailable");
RoomStore slow = new(new RoomLimits { QueueFrames = 2 });
Admission slowOwner = slow.Create(CreateBody());
using RoomStream slowStream = slow.Attach(slowOwner.RoomId, slowOwner.Token, null, null);
slow.Execute(slowOwner.RoomId, slowOwner.Token, Cmd(slowOwner, 1, "call.linkChanged", new { meetUri = (string?)null }));
slow.Execute(slowOwner.RoomId, slowOwner.Token, Cmd(slowOwner, 2, "call.linkChanged", new { meetUri = (string?)null }));
Check(slowStream.Stopped.IsCancellationRequested, "Slow consumers must disconnect instead of dropping ordered events");

// Consume the existing shared media fixtures and reject malformed JSON before dispatch.
string fixturePath = Environment.GetEnvironmentVariable("SYNCWATCH_CONTRACT_FIXTURES") ?? throw new InvalidOperationException("Shared fixtures are required");
using (JsonDocument fixtures = JsonDocument.Parse(File.ReadAllBytes(fixturePath)))
{
    int mediaCases = 0;
    foreach (JsonElement fixture in fixtures.RootElement.EnumerateArray().Where(item => item.GetProperty("schema").GetString() == "media-descriptor"))
    {
        mediaCases++;
        if (fixture.GetProperty("error").ValueKind == JsonValueKind.Null) { _ = Wire.Media(fixture.GetProperty("value")); Check(true, "Valid media fixture"); }
        else { Reject(() => Wire.Media(fixture.GetProperty("value")), fixture.GetProperty("error").GetString()!); }
    }
    Check(mediaCases == 3, "Every shared media fixture must execute");
}
Reject(() => Wire.Parse(Encoding.UTF8.GetBytes("{\"v\":1,\"v\":2}")), "malformed");
Reject(() => Wire.Parse(new byte[Wire.MaximumBytes + 1]), "frameTooLarge");
Reject(() => Wire.ReadCommand(Encoding.UTF8.GetBytes("{\"v\":1}")), "malformed");

// Real Kestrel HTTP and WebSocket transport, with header-only credentials.
SelectionChecks.Run();
AccountChecks.Run();
await AuthenticatedRoomHttpChecks.Run();
string creationKey = Wire.Secret();
await using var app = RoomApplication.Build(["--urls", "http://127.0.0.1:0"], creationKey: creationKey);
await app.StartAsync();
string address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
using HttpClient http = new() { BaseAddress = new Uri(address), Timeout = TimeSpan.FromSeconds(10) };
async Task<JsonElement> Request(HttpMethod method, string path, object? body = null, string? token = null, HttpStatusCode status = HttpStatusCode.OK)
{
    using HttpRequestMessage message = new(method, path);
    if (token is not null) { message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token); }
    if (body is not null) { message.Content = new ByteArrayContent(JsonSerializer.SerializeToUtf8Bytes(body, Wire.Json)); message.Content.Headers.ContentType = new("application/json"); }
    using HttpResponseMessage response = await http.SendAsync(message);
    Check(response.StatusCode == status, $"HTTP status for {path}: {response.StatusCode}");
    byte[] bytes = await response.Content.ReadAsByteArrayAsync();
    Check(bytes.Length <= Wire.MaximumBytes, "HTTP responses must remain bounded");
    return Wire.Parse(bytes);
}
await Request(HttpMethod.Post, "/v1/rooms", CreateBody(), status: HttpStatusCode.Unauthorized);
JsonElement admitted = await Request(HttpMethod.Post, "/v1/rooms", CreateBody(), creationKey);
Admission networkOwner = JsonSerializer.Deserialize<Admission>(admitted, Wire.Json)!;
string roomPath = "/v1/rooms/" + networkOwner.RoomId;
JsonElement joined = await Request(HttpMethod.Post, roomPath + "/join", JoinBody(networkOwner));
Admission networkGuest = JsonSerializer.Deserialize<Admission>(joined, Wire.Json)!;
JsonElement idleSelection = await Request(HttpMethod.Get, roomPath + "/selection?watchGeneration=0", token: networkOwner.Token);
Check(idleSelection.GetProperty("selection").ValueKind == JsonValueKind.Null, "Initial selection is absent");
observed.Add(new { schema = "room-selection", value = idleSelection });
await Request(HttpMethod.Get, roomPath + "/selection?watchGeneration=1", token: networkOwner.Token, status: HttpStatusCode.Conflict);
await Request(HttpMethod.Get, roomPath + "/selection?watchGeneration=00", token: networkOwner.Token, status: HttpStatusCode.BadRequest);
await Request(HttpMethod.Get, roomPath + "/selection", token: networkOwner.Token, status: HttpStatusCode.BadRequest);
await Request(HttpMethod.Get, roomPath + "/selection?watchGeneration=0", token: creationKey, status: HttpStatusCode.Unauthorized);
await Request(HttpMethod.Get, roomPath + "/snapshot", token: creationKey, status: HttpStatusCode.Unauthorized);
await Request(HttpMethod.Post, roomPath + "/invitation", new { v = 2 }, networkOwner.Token, HttpStatusCode.BadRequest);
await Request(HttpMethod.Post, roomPath + "/commands", new { v = 1, padding = new string('x', Wire.MaximumBytes) }, networkOwner.Token, HttpStatusCode.RequestEntityTooLarge);
using ClientWebSocket ws = new();
ws.Options.SetRequestHeader("Authorization", "Bearer " + networkGuest.Token);
using CancellationTokenSource deadline = new(TimeSpan.FromSeconds(10));
await ws.ConnectAsync(new Uri(address.Replace("http://", "ws://", StringComparison.Ordinal) + roomPath + "/events"), deadline.Token);
async Task<JsonElement> ReadSocket()
{
    using MemoryStream message = new();
    byte[] buffer = new byte[4096];
    while (true)
    {
        WebSocketReceiveResult receive = await ws.ReceiveAsync(buffer, deadline.Token);
        Check(receive.MessageType == WebSocketMessageType.Text, "WebSocket messages must be JSON text");
        message.Write(buffer, 0, receive.Count);
        Check(message.Length <= Wire.MaximumBytes, "WebSocket response limit");
        if (receive.EndOfMessage) { return Wire.Parse(message.ToArray()); }
    }
}
JsonElement hello = await ReadSocket();
Check(hello.GetProperty("type").GetString() == "room.bootstrap", "Initial transport snapshot");
observed.Add(new { schema = "room-snapshot", value = hello.GetProperty("data").GetProperty("snapshot").Clone() });
Command catalog = Catalog(networkGuest, 1, [Media()]);
byte[] requestBytes = JsonSerializer.SerializeToUtf8Bytes(new
{
    v = 1,
    id = catalog.Id,
    type = catalog.Type,
    clientRunId = catalog.ClientRunId,
    roomEpoch = catalog.RoomEpoch,
    watchGeneration = catalog.WatchGeneration,
    payload = catalog.Payload
}, Wire.Json);
// Fragmentation must not create a second command or partial event.
await ws.SendAsync(requestBytes.AsMemory(0, 17), WebSocketMessageType.Text, false, deadline.Token);
await ws.SendAsync(requestBytes.AsMemory(17), WebSocketMessageType.Text, true, deadline.Token);
JsonElement actualEvent = await ReadSocket();
Check(actualEvent.GetProperty("type").GetString() == "catalog.updated", "Actual catalog event over WebSocket");
observed.Add(new { schema = "room-event", value = actualEvent.Clone() });
JsonElement resultFrame = await ReadSocket();
Check(resultFrame.GetProperty("type").GetString() == "command.result" && resultFrame.GetProperty("error").ValueKind == JsonValueKind.Null, "Command acknowledgement");
await Request(HttpMethod.Post, roomPath + "/revoke", new { v = 1, memberId = networkGuest.MemberId }, networkGuest.Token, HttpStatusCode.Forbidden);
await Request(HttpMethod.Post, roomPath + "/revoke", new { v = 1, memberId = networkGuest.MemberId }, networkOwner.Token);
await Request(HttpMethod.Get, roomPath + "/snapshot", token: networkGuest.Token, status: HttpStatusCode.Unauthorized);
try
{
    WebSocketReceiveResult closed = await ws.ReceiveAsync(new byte[1], deadline.Token).WaitAsync(TimeSpan.FromSeconds(3));
    Check(closed.MessageType == WebSocketMessageType.Close, "Revocation must close the actual WebSocket transport");
}
catch (WebSocketException) { Check(true, "Revocation aborted the actual WebSocket transport"); }
using (HttpRequestMessage origin = new(HttpMethod.Get, roomPath + "/snapshot"))
{
    origin.Headers.Authorization = new("Bearer", networkOwner.Token); origin.Headers.Add("Origin", "https://example.invalid");
    using HttpResponseMessage response = await http.SendAsync(origin);
    Check(response.StatusCode == HttpStatusCode.Forbidden, "Cross-origin browser requests are not authorized");
}
await Request(HttpMethod.Post, roomPath + "/leave", token: networkOwner.Token);
await Request(HttpMethod.Get, roomPath + "/snapshot", token: networkOwner.Token, status: HttpStatusCode.NotFound);
await app.StopAsync();
string? output = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_CONTRACT_OUTPUT");
if (output is not null) { await File.WriteAllBytesAsync(output, JsonSerializer.SerializeToUtf8Bytes(observed, Wire.Json)); }
Console.WriteLine($"Passed {assertions} room metadata assertions: authorization, catalogs, conflicts, replay, expiry, limits, HTTP and WebSocket transport");

internal sealed class ManualClock : TimeProvider
{
    private DateTimeOffset now = new(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
    public override DateTimeOffset GetUtcNow() => now;
    public void Advance(TimeSpan duration) => now += duration;
}
