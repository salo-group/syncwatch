using System.Net.Http.Headers;
using System.Net.WebSockets;
using System.Text.Json;

namespace SyncWatch.Room;

/// <summary>One room attempt owns all network work. Close cancels and joins it.</summary>
public sealed partial class RoomClient : IAsyncDisposable
{
    public RoomAddress Address { get; }
    internal readonly RoomAccountAuthority? Authority;
    internal string Version => Authority is null ? "v1" : "v2";
    private readonly HttpClient client;
    private readonly CancellationTokenSource lifetime = new();
    private readonly object gate = new();
    private readonly HashSet<TaskCompletionSource> requests = [];
    private TaskCompletionSource? closing;
    private RoomEventStream? eventStream;
    private bool eventsRequested;
    public RoomClient(RoomAddress address) : this(address, null) { }
    public static RoomClient ForAccount(RoomAccountAuthority authority) => new(authority.Address, authority);
    private RoomClient(RoomAddress address, RoomAccountAuthority? authority)
    {
        Address = address; Authority = authority;
        client = new HttpClient(new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseCookies = false,
            Credentials = null,
            PreAuthenticate = false,
            MaxConnectionsPerServer = 2,
            MaxResponseHeadersLength = 16,
            ConnectTimeout = TimeSpan.FromSeconds(10)
        })
        { Timeout = TimeSpan.FromSeconds(20), MaxResponseContentBufferSize = RoomWire.MaximumBytes };
    }
    public async Task<RoomAdmission> CreateAsync(RoomIdentity identity, RoomConnection connection, RoomSecret creationKey, CancellationToken cancellation = default)
    {
        if (Authority is not null) throw RoomWire.Bad();
        byte[] body = RoomWire.Encode(new
        {
            v = 1,
            clientRunId = identity.ClientRunId,
            syncplayUsername = identity.SyncplayUsername,
            syncplayHost = connection.Host,
            syncplayPort = connection.Port,
            syncplayRoom = connection.Room
        });
        return RoomAdmission.Decode(await RequestAsync(HttpMethod.Post, "v1/rooms", creationKey, body, cancellation), identity, true);
    }
    public async Task<RoomAdmission> JoinAsync(string roomId, RoomSecret invitation, RoomIdentity identity, CancellationToken cancellation = default)
    {
        if (Authority is not null) throw RoomWire.Bad();
        RoomWire.Id(roomId);
        byte[] body = RoomWire.Encode(new { v = 1, invitation = invitation.Value, clientRunId = identity.ClientRunId, syncplayUsername = identity.SyncplayUsername });
        return RoomAdmission.Decode(await RequestAsync(HttpMethod.Post, $"v1/rooms/{roomId}/join", null, body, cancellation), identity, false);
    }
    public async Task<RoomBootstrap> SnapshotAsync(RoomAdmission admission, CancellationToken cancellation = default) =>
        RoomBootstrap.Decode(await RequestAsync(HttpMethod.Get, $"{Version}/rooms/{admission.RoomId}/snapshot", null, null, cancellation, admission: admission));
    public async Task<RoomCatalogPage> CatalogAsync(RoomAdmission admission, RoomBootstrap bootstrap, string cursor, CancellationToken cancellation = default)
    {
        if (bootstrap.Snapshot.Epoch != admission.Bootstrap.Snapshot.Epoch) throw RoomWire.Bad();
        _ = new RoomCatalogPosition(cursor, bootstrap);
        return RoomCatalogPage.Decode(await RequestAsync(HttpMethod.Get, $"{Version}/rooms/{admission.RoomId}/catalog", null, null, cancellation, admission: admission, cursor: cursor), bootstrap, cursor);
    }
    /// <summary>The caller serializes bootstrap creation with this walk and bounds its overall lifetime.</summary>
    public Task<RoomCatalogView> CatalogViewAsync(RoomAdmission admission, RoomBootstrap bootstrap, CancellationToken cancellation = default)
    {
        if (bootstrap.Snapshot.Epoch != admission.Bootstrap.Snapshot.Epoch) throw RoomWire.Bad();
        return RoomCatalogView.ReadAsync(bootstrap,
            (cursor, token) => RequestAsync(HttpMethod.Get, $"{Version}/rooms/{admission.RoomId}/catalog", null, null, token, cursor: cursor, admission: admission), cancellation);
    }
    public async Task<RoomSelectionDefinition> SelectionAsync(RoomAdmission admission, RoomBootstrap bootstrap, CancellationToken cancellation = default)
    {
        if (bootstrap.Snapshot.Epoch != admission.Bootstrap.Snapshot.Epoch) throw RoomWire.Bad();
        return RoomSelectionDefinition.Decode(await RequestAsync(HttpMethod.Get, $"{Version}/rooms/{admission.RoomId}/selection",
            null, null, cancellation, admission: admission, generation: bootstrap.Snapshot.WatchGeneration), bootstrap);
    }
    public async Task LeaveAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        using var document = RoomWire.Parse(await RequestAsync(HttpMethod.Post, $"{Version}/rooms/{admission.RoomId}/leave", null, null, cancellation, admission: admission));
        if (Authority is not null) { RoomAccessWire.Status(RoomWire.Encode(document.RootElement), "left"); return; }
        var value = document.RootElement; RoomWire.Keys(value, "v", "status");
        if (RoomWire.Integer(value, "v", 1) != 1 || RoomWire.String(value, "status") != "left") throw RoomWire.Bad();
    }
    public async Task<RoomCommandResult> ExecuteAsync(RoomAdmission admission, RoomCommand command, CancellationToken cancellation = default)
    {
        command.Verify(admission);
        return RoomCommandResult.Decode(await RequestAsync(HttpMethod.Post, $"{Version}/rooms/{admission.RoomId}/commands", null, command.EncodedBytes, cancellation, admission: admission), command);
    }
    /// <summary>Attach immediately after admission, before hashing media.</summary>
    public async Task<RoomEventStream> OpenEventsAsync(RoomAdmission admission, CancellationToken cancellation = default)
    {
        TaskCompletionSource completed = new(TaskCreationOptions.RunContinuationsAsynchronously);
        CancellationTokenSource linked;
        lock (gate)
        {
            if (closing is not null || eventsRequested) throw new RoomProtocolException("closed");
            eventsRequested = true; requests.Add(completed);
            linked = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token, cancellation);
        }
        var socket = new ClientWebSocket();
        try
        {
            Validate(admission);
            AccountRoomCredential? credential = Authority is null ? null : await Authority.CredentialAsync(linked.Token);
            socket.Options.SetRequestHeader("Authorization", "Bearer " + (credential?.Access ?? admission.Token!.Value));
            if (credential is not null)
            {
                socket.Options.SetRequestHeader("X-SyncWatch-Member", admission.MemberId);
                socket.Options.SetRequestHeader("X-SyncWatch-Run", Run(admission));
            }
            var uri = new UriBuilder(Address.Endpoint($"{Version}/rooms/{admission.RoomId}/events")) { Scheme = Address.Origin.Scheme == "https" ? "wss" : "ws" }.Uri;
            linked.CancelAfter(TimeSpan.FromSeconds(20));
            await socket.ConnectAsync(uri, client, linked.Token).ConfigureAwait(false);
            lock (gate)
            {
                linked.Token.ThrowIfCancellationRequested();
                return eventStream = new RoomEventStream(socket);
            }
        }
        catch { socket.Dispose(); throw; }
        finally { linked.Dispose(); lock (gate) requests.Remove(completed); completed.SetResult(); }
    }
    public ValueTask DisposeAsync()
    {
        TaskCompletionSource completion; Task[] pending;
        lock (gate)
        {
            if (closing is not null) return new(closing.Task);
            closing = completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
            pending = requests.Select(p => p.Task).ToArray();
        }
        _ = CloseCoreAsync(pending, completion);
        return new(completion.Task);
    }
    private async Task CloseCoreAsync(Task[] pending, TaskCompletionSource completion)
    {
        try
        {
            await lifetime.CancelAsync(); client.Dispose();
            await Task.WhenAll(pending);
            if (eventStream is not null) await eventStream.DisposeAsync();
            lifetime.Dispose(); completion.SetResult();
        }
        catch (Exception) { completion.SetException(new RoomProtocolException("transport")); }
    }
    private static string Run(RoomAdmission admission) => admission.Bootstrap.Snapshot.Members.FirstOrDefault(p => p.MemberId == admission.MemberId)?.Identity.ClientRunId ?? throw RoomWire.Bad();
    private void Validate(RoomAdmission admission)
    {
        RoomWire.Id(admission.RoomId); RoomWire.Id(admission.MemberId); _ = Run(admission);
        if (Authority is null ? admission.AccountScope is not null || admission.Token is null
            : admission.AccountScope != Authority.Scope || admission.Token is not null) throw RoomWire.Bad();
    }
    internal async Task<byte[]> RequestAsync(HttpMethod method, string path, RoomSecret? legacyCredential, byte[]? body, CancellationToken cancellation, string? cursor = null, long? generation = null, RoomAdmission? admission = null, string? run = null)
    {
        cancellation.ThrowIfCancellationRequested();
        if (admission is not null) Validate(admission);
        TaskCompletionSource completed = new(TaskCreationOptions.RunContinuationsAsynchronously);
        CancellationTokenSource stop;
        lock (gate)
        {
            if (closing is not null) throw new RoomProtocolException("closed");
            stop = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token, cancellation); requests.Add(completed);
        }
        try
        {
            var uri = Address.Endpoint(path);
            if (cursor is not null) uri = new UriBuilder(uri) { Query = "cursor=" + Uri.EscapeDataString(cursor) }.Uri;
            if (generation is not null) uri = new UriBuilder(uri) { Query = "watchGeneration=" + generation.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) }.Uri;
            stop.CancelAfter(TimeSpan.FromSeconds(20));
            AccountRoomCredential? credential = Authority is null ? null : await Authority.CredentialAsync(stop.Token);
            for (int attempt = 0; ; attempt++)
            {
                using var request = new HttpRequestMessage(method, uri);
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                string? access = credential?.Access ?? admission?.Token?.Value;
                // The legacy credential is handled separately; account access is never persisted.
                access ??= legacyCredential?.Value;
                if (access is not null) request.Headers.Authorization = new("Bearer", access);
                if (admission is not null && Authority is not null)
                {
                    request.Headers.Add("X-SyncWatch-Member", admission.MemberId);
                    request.Headers.Add("X-SyncWatch-Run", Run(admission));
                }
                else if (run is not null) { RoomWire.Id(run); request.Headers.Add("X-SyncWatch-Run", run); }
                if (body is not null) { request.Content = new ByteArrayContent(body); request.Content.Headers.ContentType = new("application/json"); }
                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseContentRead, stop.Token).ConfigureAwait(false);
                if (response.RequestMessage?.RequestUri != request.RequestUri || response.Content.Headers.ContentType?.MediaType != "application/json") throw new RoomProtocolException("transport");
                byte[] bytes = await response.Content.ReadAsByteArrayAsync(stop.Token).ConfigureAwait(false);
                if (response.StatusCode == System.Net.HttpStatusCode.Unauthorized && attempt == 0 && credential is not null && Authority is not null)
                {
                    AccountRoomCredential current = await Authority.CredentialAsync(stop.Token);
                    if (current.Access != credential.Access) { credential = current; continue; }
                }
                if (!response.IsSuccessStatusCode)
                {
                    using var document = RoomWire.Parse(bytes); var value = document.RootElement;
                    RoomWire.Keys(value, Authority is null ? ["error"] : ["v", "error"]);
                    if (Authority is not null && RoomWire.Integer(value, "v", 2) != 2) throw RoomWire.Bad();
                    var error = value.GetProperty("error"); RoomWire.Keys(error, "code");
                    string code = RoomWire.String(error, "code");
                    throw new RoomProtocolException(code is "unauthorized" or "forbidden" or "notFound" or "conflict" or "expired" or "queueFull" or "tlsRequired" or "malformed" or "frameTooLarge" or "internal" or "snapshotRequired" or "duplicate" or "staleGeneration" or "accountSignInRequired" or "admissionRequired" or "unavailable" ? code : "transport");
                }
                stop.Token.ThrowIfCancellationRequested(); return Authority is null ? bytes : RoomAccessWire.Result(bytes);
            }
        }
        finally { stop.Dispose(); lock (gate) requests.Remove(completed); completed.SetResult(); }
    }
}
