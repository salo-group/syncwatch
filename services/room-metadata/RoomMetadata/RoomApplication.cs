using System.Net;
using System.Text.Json;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata;

public static class RoomApplication
{
    public static WebApplication Build(string[] args, RoomStore? store = null, string? creationKey = null, RoomLimits? limits = null,
        AccountOptions? accounts = null, RoomAuthorityOptions? authority = null)
    {
        accounts ??= AccountOptions.FromEnvironment();
        authority ??= RoomAuthorityOptions.FromEnvironment();
        Wire.Need(authority is null || accounts is not null, "roomAccountsRequired", 503);
        creationKey ??= Environment.GetEnvironmentVariable("SYNCWATCH_CREATE_KEY");
        if (authority is null) Wire.Need(creationKey is { Length: >= 32 and <= 256 }, "creationKeyRequired");
        byte[] creationHash = authority is null ? Wire.Hash(creationKey!) : [];
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
        if (authority is null)
        {
            builder.Services.AddSingleton(store ?? new RoomStore(limits));
            builder.Services.AddHostedService<RoomCleanup>();
        }
        if (accounts is not null) AccountModule.Configure(builder, accounts);
        if (authority is not null) AuthenticatedRoomModule.Configure(builder, authority, limits);
        WebApplication app = builder.Build();
        app.Use(async (context, next) =>
        {
            context.Response.Headers.CacheControl = "no-store";
            try
            {
                Wire.Need(context.Request.IsHttps || (context.Connection.RemoteIpAddress is IPAddress remote
                    && context.Connection.LocalIpAddress is IPAddress local && IPAddress.IsLoopback(remote) && IPAddress.IsLoopback(local)), "tlsRequired", 403);
                // Native clients use header credentials. Cross-origin browser access is not enabled.
                Wire.Need(!context.Request.Headers.ContainsKey("Origin"), "forbidden", 403);
                if (accounts is not null && (context.Request.Path.StartsWithSegments("/auth") || context.Request.Path.StartsWithSegments("/v2")))
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
                await Error(context, error.Code);
            }
            catch (BadHttpRequestException error) when (!context.Response.HasStarted)
            {
                context.Response.StatusCode = error.StatusCode;
                await Error(context, error.StatusCode == 413 ? "frameTooLarge" : "malformed");
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested) { }
            catch (Exception)
            {
                Console.Error.WriteLine("Room metadata request failed.");
                if (context.Response.HasStarted) { context.Abort(); }
                else
                {
                    context.Response.StatusCode = 500;
                    await Error(context, "internal");
                }
            }
        });
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(20), KeepAliveTimeout = TimeSpan.FromSeconds(10) });
        app.MapGet("/health", context => Write(context, new { v = 1, status = "ok" }));
        if (authority is null)
        {
            RoomStore rooms = app.Services.GetRequiredService<RoomStore>();
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
        }
        if (accounts is not null) AccountModule.Map(app, accounts);
        if (authority is not null) AuthenticatedRoomModule.Map(app);
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

    internal static async Task<byte[]> Body(HttpContext context)
    {
        Wire.Need(context.Request.ContentType?.Split(';')[0].Trim().Equals("application/json", StringComparison.OrdinalIgnoreCase) == true);
        byte[] buffer = new byte[Wire.MaximumBytes + 1];
        int count = await context.Request.Body.ReadAtLeastAsync(buffer, buffer.Length, throwOnEndOfStream: false, context.RequestAborted);
        Wire.Need(count <= Wire.MaximumBytes, "frameTooLarge", 413);
        return buffer[..count];
    }

    internal static async Task Write(HttpContext context, object value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json);
        Wire.Need(bytes.Length <= Wire.MaximumBytes, "frameTooLarge", 413);
        context.Response.ContentType = "application/json; charset=utf-8";
        await context.Response.Body.WriteAsync(bytes, context.RequestAborted);
    }

    private static Task Error(HttpContext context, string code) => context.Request.Path.StartsWithSegments("/v2")
        ? Write(context, new { v = 2, error = new { code } }) : Write(context, new { error = new { code } });

    private static Task Events(HttpContext context, RoomStore store)
    {
        string room = RoomId(context), token = Bearer(context);
        return RoomEventTransport.Run(context, (epoch, cursor) => store.Attach(room, token, epoch, cursor),
            bytes => store.Execute(room, token, Wire.ReadCommand(bytes)), stream => store.Detach(room, stream));
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
