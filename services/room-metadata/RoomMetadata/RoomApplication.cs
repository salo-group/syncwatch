using System.Net;
using System.Net.WebSockets;
using System.Text.Json;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata;

public static class RoomApplication
{
    public static WebApplication Build(string[] args, RoomStore? store = null, string? creationKey = null, RoomLimits? limits = null)
    {
        creationKey ??= Environment.GetEnvironmentVariable("SYNCWATCH_CREATE_KEY");
        Wire.Need(creationKey is { Length: >= 32 and <= 256 }, "creationKeyRequired");
        byte[] creationHash = Wire.Hash(creationKey!);
        WebApplicationBuilder builder = WebApplication.CreateBuilder(args);
        if (builder.Configuration["urls"] is null) { builder.WebHost.UseUrls("http://127.0.0.1:8788"); }
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options =>
        {
            options.Limits.MaxRequestBodySize = Wire.MaximumBytes;
            options.Limits.MaxConcurrentConnections = 128;
            options.Limits.MaxConcurrentUpgradedConnections = 128;
            options.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(10);
            options.Limits.KeepAliveTimeout = TimeSpan.FromSeconds(30);
        });
        limits ??= builder.Configuration.GetSection("SyncWatch:Limits").Get<RoomLimits>() ?? new();
        builder.Services.AddSingleton(store ?? new RoomStore(limits));
        builder.Services.AddHostedService<RoomCleanup>();
        AccountOptions? accounts = AccountOptions.FromEnvironment();
        if (accounts is not null) AccountModule.Configure(builder, accounts);
        WebApplication app = builder.Build();
        RoomStore rooms = app.Services.GetRequiredService<RoomStore>();
        app.Use(async (context, next) =>
        {
            context.Response.Headers.CacheControl = "no-store";
            try
            {
                Wire.Need(context.Request.IsHttps || (context.Connection.RemoteIpAddress is IPAddress remote
                    && context.Connection.LocalIpAddress is IPAddress local && IPAddress.IsLoopback(remote) && IPAddress.IsLoopback(local)), "tlsRequired", 403);
                // Native clients use header credentials. Cross-origin browser access is not enabled.
                Wire.Need(!context.Request.Headers.ContainsKey("Origin"), "forbidden", 403);
                if (accounts is not null && (context.Request.Path.StartsWithSegments("/auth") || context.Request.Path.StartsWithSegments("/v2/account")))
                    AccountModule.ValidateOrigin(context, accounts);
                await next(context);
            }
            catch (AccountException error) when (!context.Response.HasStarted)
            {
                context.Response.StatusCode = error.Status;
                await Write(context, new { v = 2, error = new { code = error.Code } });
            }
            catch (RoomException error) when (!context.Response.HasStarted)
            {
                context.Response.StatusCode = error.Status;
                await Write(context, new { error = new { code = error.Code } });
            }
            catch (BadHttpRequestException error) when (!context.Response.HasStarted)
            {
                context.Response.StatusCode = error.StatusCode;
                await Write(context, new { error = new { code = error.StatusCode == 413 ? "frameTooLarge" : "malformed" } });
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested) { }
            catch (Exception)
            {
                Console.Error.WriteLine("Room metadata request failed.");
                if (context.Response.HasStarted) { context.Abort(); }
                else
                {
                    context.Response.StatusCode = 500;
                    await Write(context, new { error = new { code = "internal" } });
                }
            }
        });
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(20), KeepAliveTimeout = TimeSpan.FromSeconds(10) });
        app.MapGet("/health", context => Write(context, new { v = 1, status = "ok" }));
        app.MapPost("/v1/rooms", async context =>
        {
            Wire.Need(Wire.SecretMatches(Bearer(context), creationHash), "unauthorized", 401);
            await Write(context, rooms.Create(Wire.Parse(await Body(context))));
        });
        app.MapPost("/v1/rooms/{roomId}/join", async context => await Write(context,
            rooms.Join(RoomId(context), Wire.Parse(await Body(context)))));
        app.MapGet("/v1/rooms/{roomId}/snapshot", context => Write(context, rooms.Snapshot(RoomId(context), Bearer(context))));
        app.MapGet("/v1/rooms/{roomId}/catalog", context => Write(context,
            rooms.Catalog(RoomId(context), Bearer(context), context.Request.Query["cursor"].ToString())));
        app.MapGet("/v1/rooms/{roomId}/selection", context =>
        {
            string raw = context.Request.Query["watchGeneration"].ToString();
            Wire.Need(long.TryParse(raw, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture,
                out long generation) && generation.ToString(System.Globalization.CultureInfo.InvariantCulture) == raw);
            return Write(context, rooms.Selection(RoomId(context), Bearer(context), generation));
        });
        app.MapPost("/v1/rooms/{roomId}/invitation", context =>
        {
            EmptyBody(context);
            return Write(context, rooms.RotateInvitation(RoomId(context), Bearer(context)));
        });
        app.MapPost("/v1/rooms/{roomId}/leave", async context =>
        {
            EmptyBody(context);
            rooms.Leave(RoomId(context), Bearer(context));
            await Write(context, new { v = 1, status = "left" });
        });
        app.MapPost("/v1/rooms/{roomId}/revoke", async context =>
        {
            JsonElement body = Wire.Parse(await Body(context));
            Wire.Fields(body, "v", "memberId"); Wire.Version(body);
            string member = Wire.String(body, "memberId"); Wire.Need(Wire.Id(member));
            rooms.Leave(RoomId(context), Bearer(context), member);
            await Write(context, new { v = 1, status = "revoked" });
        });
        app.MapPost("/v1/rooms/{roomId}/commands", async context => await Write(context,
            rooms.Execute(RoomId(context), Bearer(context), Wire.ReadCommand(await Body(context)))));
        app.MapGet("/v1/rooms/{roomId}/events", context => Events(context, rooms));
        if (accounts is not null) AccountModule.Map(app, accounts);
        return app;
    }

    private static string RoomId(HttpContext context) => (string)context.Request.RouteValues["roomId"]!;
    private static string Bearer(HttpContext context)
    {
        string value = context.Request.Headers.Authorization.ToString();
        Wire.Need(value.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase) && value.Length is >= 8 and <= 263, "unauthorized", 401);
        return value[7..];
    }

    private static void EmptyBody(HttpContext context) => Wire.Need(
        context.Features.Get<Microsoft.AspNetCore.Http.Features.IHttpRequestBodyDetectionFeature>()?.CanHaveBody != true);

    private static async Task<byte[]> Body(HttpContext context)
    {
        Wire.Need(context.Request.ContentType?.Split(';')[0].Trim().Equals("application/json", StringComparison.OrdinalIgnoreCase) == true);
        byte[] buffer = new byte[Wire.MaximumBytes + 1];
        int count = await context.Request.Body.ReadAtLeastAsync(buffer, buffer.Length, throwOnEndOfStream: false, context.RequestAborted);
        Wire.Need(count <= Wire.MaximumBytes, "frameTooLarge", 413);
        return buffer[..count];
    }

    private static async Task Write(HttpContext context, object value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json);
        Wire.Need(bytes.Length <= Wire.MaximumBytes, "frameTooLarge", 413);
        context.Response.ContentType = "application/json; charset=utf-8";
        await context.Response.Body.WriteAsync(bytes, context.RequestAborted);
    }

    private static async Task Events(HttpContext context, RoomStore store)
    {
        Wire.Need(context.WebSockets.IsWebSocketRequest, "webSocketRequired", 400);
        Wire.Need(context.Request.Query.Keys.All(key => key is "epoch" or "cursor"));
        string? epoch = context.Request.Query.ContainsKey("epoch") ? context.Request.Query["epoch"].ToString() : null;
        long? cursor = null;
        if (context.Request.Query.ContainsKey("cursor"))
        {
            Wire.Need(long.TryParse(context.Request.Query["cursor"], System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture, out long parsed) && parsed <= Wire.MaximumCounter);
            cursor = parsed;
        }
        string room = RoomId(context), token = Bearer(context);
        using RoomStream stream = store.Attach(room, token, epoch, cursor);
        try
        {
            using WebSocket socket = await context.WebSockets.AcceptWebSocketAsync();
            using CancellationTokenSource stop = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted, stream.Stopped.Token,
                context.RequestServices.GetRequiredService<IHostApplicationLifetime>().ApplicationStopping);
            Task sender = Send(socket, stream, stop.Token);
            try
            {
                while (!stop.IsCancellationRequested)
                {
                    byte[]? bytes = await Receive(socket, stop.Token);
                    if (bytes is null) { break; }
                    try { stream.Enqueue(store.Execute(room, token, Wire.ReadCommand(bytes))); }
                    catch (RoomException error) { stream.Enqueue(new { v = 1, type = "room.error", error = new { code = error.Code } }); }
                }
            }
            catch (Exception error) when (error is OperationCanceledException or WebSocketException or RoomException) { }
            finally
            {
                stop.Cancel();
                try { await sender; } catch (Exception error) when (error is OperationCanceledException or WebSocketException) { }
                if (socket.State == WebSocketState.CloseReceived)
                {
                    using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(2));
                    try { await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "closed", timeout.Token); }
                    catch (Exception error) when (error is OperationCanceledException or WebSocketException) { }
                }
                socket.Abort();
            }
        }
        finally { store.Detach(room, stream); }
    }

    private static async Task Send(WebSocket socket, RoomStream stream, CancellationToken token)
    {
        await foreach (byte[] bytes in stream.Frames.Reader.ReadAllAsync(token))
        {
            await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, token);
        }
    }

    private static async Task<byte[]?> Receive(WebSocket socket, CancellationToken token)
    {
        using MemoryStream bytes = new();
        byte[] buffer = new byte[4096];
        while (true)
        {
            WebSocketReceiveResult result = await socket.ReceiveAsync(buffer, token);
            if (result.MessageType == WebSocketMessageType.Close) { return null; }
            Wire.Need(result.MessageType == WebSocketMessageType.Text);
            Wire.Need(bytes.Length + result.Count <= Wire.MaximumBytes, "frameTooLarge", 413);
            bytes.Write(buffer, 0, result.Count);
            if (result.EndOfMessage) { return bytes.ToArray(); }
        }
    }
}

internal sealed class RoomCleanup(RoomStore store) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using PeriodicTimer timer = new(TimeSpan.FromSeconds(1));
        while (await timer.WaitForNextTickAsync(stoppingToken)) { store.Sweep(); }
    }
}
