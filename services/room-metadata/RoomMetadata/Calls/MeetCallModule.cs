using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata.Calls;

internal static class MeetCallModule
{
    internal static void Configure(WebApplicationBuilder builder, AccountOptions options)
    {
        builder.Services.AddSingleton(provider => new MeetCallJournal(options.Directory, provider.GetRequiredService<IDataProtectionProvider>()));
        builder.Services.AddSingleton<IMeetProvider>(provider => new MeetGoogleProvider(provider.GetRequiredService<MeetGrantRepository>(), options));
        builder.Services.AddSingleton(provider => new MeetCallCoordinator(provider.GetRequiredService<AuthenticatedRooms>(),
            provider.GetRequiredService<MeetGrantRepository>(), provider.GetRequiredService<MeetCallJournal>(), provider.GetRequiredService<IMeetProvider>()));
        builder.Services.AddHostedService(provider => provider.GetRequiredService<MeetCallCoordinator>());
    }
    internal static void Map(WebApplication app)
    {
        _ = app.Services.GetRequiredService<MeetCallJournal>();
        var group = app.MapGroup("/v2/rooms/{roomId}/call").RequireRateLimiting("account");
        group.MapGet("", context =>
        {
            AccountWire.Need(context.Request.Query.Count == 0 && context.Request.ContentLength is null or 0);
            var (rooms, principal, room, member, run) = AuthenticatedRoomModule.Member(context);
            var scope = rooms.Access(principal, room, member, run);
            return AccountModule.Reply(context, context.RequestServices.GetRequiredService<MeetCallCoordinator>().Get(scope));
        });
        group.MapPost("/start", async context =>
        {
            var (rooms, principal, room, member, run) = AuthenticatedRoomModule.Member(context);
            var body = await AuthenticatedRoomModule.Read(context, "requestId");
            var scope = rooms.Access(principal, room, member, run, owner: true);
            AccountWire.Need(body.GetProperty("requestId").ValueKind == System.Text.Json.JsonValueKind.String);
            await AccountModule.Reply(context, context.RequestServices.GetRequiredService<MeetCallCoordinator>().Start(scope, body.GetProperty("requestId").GetString()!));
        });
        group.MapPost("/end", async context =>
        {
            var (rooms, principal, room, member, run) = AuthenticatedRoomModule.Member(context);
            var body = await AuthenticatedRoomModule.Read(context, "callId", "generation", "requestId");
            AccountWire.Need(body.GetProperty("callId").ValueKind == System.Text.Json.JsonValueKind.String
                && body.GetProperty("requestId").ValueKind == System.Text.Json.JsonValueKind.String
                && body.GetProperty("generation").ValueKind == System.Text.Json.JsonValueKind.Number);
            AccountWire.Need(body.GetProperty("generation").TryGetInt64(out long generation) && generation is > 0 and <= Wire.MaximumCounter);
            var scope = rooms.Access(principal, room, member, run, owner: true);
            await AccountModule.Reply(context, context.RequestServices.GetRequiredService<MeetCallCoordinator>().End(scope,
                body.GetProperty("callId").GetString()!, generation, body.GetProperty("requestId").GetString()!));
        });
    }
}
