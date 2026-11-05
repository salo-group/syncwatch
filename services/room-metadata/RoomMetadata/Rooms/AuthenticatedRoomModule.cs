using System.Text.Json;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Rooms;

/// <summary>Account admission is v2; the existing ordered watch-content protocol stays v1.</summary>
internal static class AuthenticatedRoomModule
{
    public static void Configure(WebApplicationBuilder builder, RoomAuthorityOptions options, RoomLimits limits)
    {
        options.Validate();
        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton(provider => new AuthenticatedRooms(provider.GetRequiredService<AccountRepository>(),
            options.PublicHost, options.Port, limits));
        builder.Services.AddHostedService<RoomAuthorityHost>();
    }

    public static void Map(WebApplication app)
    {
        var group = app.MapGroup("/v2").RequireRateLimiting("account");
        group.MapPost("/rooms", async context =>
        {
            (AuthenticatedRooms rooms, AccountPrincipal principal) = Identity(context);
            JsonElement body = await Read(context, "clientRunId", "requestId");
            string name = context.RequestServices.GetRequiredService<AccountRepository>().Profile(principal).Name;
            await Reply(context, rooms.Create(principal, name, Id(body, "clientRunId"), Id(body, "requestId")));
        });
        group.MapPost("/rooms/{roomId}/admission-requests", async context =>
        {
            (AuthenticatedRooms rooms, AccountPrincipal principal) = Identity(context);
            JsonElement body = await Read(context, "clientRunId", "requestId", "invitation");
            string name = context.RequestServices.GetRequiredService<AccountRepository>().Profile(principal).Name;
            await Reply(context, rooms.RequestAdmission(principal, name, Room(context), Id(body, "clientRunId"),
                Id(body, "requestId"), Text(body, "invitation")));
        });
        group.MapGet("/room-admissions/{requestId}", context =>
        {
            (AuthenticatedRooms rooms, AccountPrincipal principal) = Identity(context);
            Query(context);
            return Reply(context, rooms.Status(principal, RouteId(context, "requestId"), Run(context)));
        });
        group.MapPost("/room-admissions/{requestId}/accept", async context =>
        {
            (AuthenticatedRooms rooms, AccountPrincipal principal) = Identity(context);
            JsonElement body = await Read(context, "clientRunId");
            await Reply(context, rooms.Accept(principal, RouteId(context, "requestId"), Id(body, "clientRunId")));
        });
        group.MapGet("/rooms/{roomId}/pending", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Query(context);
            return Reply(context, rooms.Pending(principal, room, member, run));
        });
        group.MapPost("/rooms/{roomId}/decision", async context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            JsonElement body = await Read(context, "approvalId", "approve");
            Wire.Need(body.GetProperty("approve").ValueKind is JsonValueKind.True or JsonValueKind.False, "malformed", 400);
            rooms.Decide(principal, room, member, run, Id(body, "approvalId"), body.GetProperty("approve").GetBoolean());
            await Reply(context, new { status = "decided" });
        });
        group.MapPost("/rooms/{roomId}/invitation", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Empty(context);
            return Reply(context, rooms.Invite(principal, room, member, run));
        });
        group.MapGet("/rooms/{roomId}/snapshot", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Query(context);
            return Reply(context, rooms.Snapshot(principal, room, member, run));
        });
        group.MapGet("/rooms/{roomId}/catalog", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Query(context, "cursor");
            return Reply(context, rooms.Catalog(principal, room, member, run, context.Request.Query["cursor"].ToString()));
        });
        group.MapGet("/rooms/{roomId}/selection", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Query(context, "watchGeneration");
            string raw = context.Request.Query["watchGeneration"].ToString();
            Wire.Need(long.TryParse(raw, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture,
                out long generation) && generation >= 0 && generation <= Wire.MaximumCounter
                && generation.ToString(System.Globalization.CultureInfo.InvariantCulture) == raw, "malformed", 400);
            return Reply(context, rooms.Selection(principal, room, member, run, generation));
        });
        group.MapPost("/rooms/{roomId}/sync-ticket", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Empty(context);
            return Reply(context, rooms.Synchronize(principal, room, member, run));
        });
        group.MapPost("/rooms/{roomId}/leave", async context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Empty(context);
            rooms.Leave(principal, room, member, run);
            await Reply(context, new { status = "left" });
        });
        group.MapPost("/rooms/{roomId}/revoke", async context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            JsonElement body = await Read(context, "memberId");
            rooms.Leave(principal, room, member, run, Id(body, "memberId"));
            await Reply(context, new { status = "revoked" });
        });
        group.MapPost("/rooms/{roomId}/commands", async context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            Query(context);
            await Reply(context, rooms.Execute(principal, room, member, run, Wire.ReadCommand(await RoomApplication.Body(context))));
        });
        group.MapGet("/rooms/{roomId}/events", context =>
        {
            var (rooms, principal, room, member, run) = Member(context);
            return RoomEventTransport.Run(context, (epoch, cursor) => rooms.Attach(principal, room, member, run, epoch, cursor),
                bytes => rooms.Execute(principal, room, member, run, Wire.ReadCommand(bytes)), stream => rooms.Detach(room, stream));
        });
    }

    private static (AuthenticatedRooms, AccountPrincipal) Identity(HttpContext context) =>
        (context.RequestServices.GetRequiredService<AuthenticatedRooms>(),
         context.RequestServices.GetRequiredService<AccountRepository>().Authorize(AccountModule.Bearer(context)));
    private static (AuthenticatedRooms, AccountPrincipal, string, string, string) Member(HttpContext context)
    {
        (AuthenticatedRooms rooms, AccountPrincipal principal) = Identity(context);
        return (rooms, principal, Room(context), Header(context, "X-SyncWatch-Member"), Run(context));
    }
    private static string Run(HttpContext context) => Header(context, "X-SyncWatch-Run");
    private static string Header(HttpContext context, string name)
    {
        Wire.Need(context.Request.Headers.TryGetValue(name, out var values) && values.Count == 1 && Wire.Id(values[0]), "malformed", 400);
        return values[0]!;
    }
    private static string Room(HttpContext context) => RouteId(context, "roomId");
    private static string RouteId(HttpContext context, string name)
    {
        string value = (string)context.Request.RouteValues[name]!;
        Wire.Need(Wire.Id(value), "malformed", 400);
        return value;
    }
    private static string Text(JsonElement body, string name)
    {
        Wire.Need(body.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String, "malformed", 400);
        return value.GetString()!;
    }
    private static string Id(JsonElement body, string name)
    {
        string value = Text(body, name);
        Wire.Need(Wire.Id(value), "malformed", 400);
        return value;
    }
    private static void Query(HttpContext context, params string[] names) => Wire.Need(
        context.Request.Query.All(item => names.Contains(item.Key, StringComparer.Ordinal) && item.Value.Count == 1), "malformed", 400);
    private static void Empty(HttpContext context)
    {
        Query(context);
        Wire.Need(context.Features.Get<Microsoft.AspNetCore.Http.Features.IHttpRequestBodyDetectionFeature>()?.CanHaveBody != true, "malformed", 400);
    }
    private static async Task<JsonElement> Read(HttpContext context, params string[] names)
    {
        Query(context);
        try
        {
            JsonElement body = Wire.Parse(await RoomApplication.Body(context));
            Wire.Fields(body, ["v", .. names]);
            Wire.Need(Wire.Integer(body, "v") == 2, "unsupportedVersion", 400);
            return body;
        }
        catch (RoomException error) when (error.Code == "malformed") { throw new RoomException("malformed", 400); }
    }
    private static Task Reply(HttpContext context, object result) => RoomApplication.Write(context, new { v = 2, result });
}
