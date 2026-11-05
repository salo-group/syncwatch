using System.Text.Json;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.OAuth;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.WebUtilities;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata.Calls;

internal static class MeetAuthorizationModule
{
    internal const string PurposeKey = "syncwatch.purpose";
    private static readonly object ProposalKey = new();
    private sealed record Proposal(string Subject, string? RefreshToken, bool ScopeGranted)
    { public override string ToString() => "[provider permission proposal redacted]"; }

    internal static void Configure(WebApplicationBuilder builder, AccountOptions options)
    {
        builder.Services.AddSingleton(provider => new MeetGrantRepository(provider.GetRequiredService<AccountRepository>(),
            options.Directory, provider.GetRequiredService<IDataProtectionProvider>()));
        builder.Services.AddSingleton(provider => new MeetAuthorizationTransactions(provider.GetRequiredService<AccountRepository>(),
            provider.GetRequiredService<AuthenticatedRooms>(), provider.GetRequiredService<MeetGrantRepository>(), options.PublicOrigin));
        builder.Services.AddHostedService<MeetAuthorizationCleanup>();
    }
    internal static bool Requested(AuthenticationProperties? properties) => properties?.Items.TryGetValue(PurposeKey, out string? purpose) == true && purpose == "meet";
    internal static string AuthorizationUri(string uri, AuthenticationProperties properties) => Requested(properties)
        ? QueryHelpers.AddQueryString(uri, new Dictionary<string, string?>
        { ["access_type"] = "offline", ["prompt"] = "consent select_account", ["include_granted_scopes"] = "true" }) : uri;

    // HTTP-context items are request-local; tokens never enter claims, authentication properties or browser cookies.
    internal static void Propose(OAuthCreatingTicketContext context, string subject)
    {
        if (!Requested(context.Properties)) return;
        JsonDocument? response = context.TokenResponse.Response;
        bool granted = response is not null && response.RootElement.TryGetProperty("scope", out var scope)
            && scope.ValueKind == JsonValueKind.String && (scope.GetString() ?? "").Split(' ', StringSplitOptions.RemoveEmptyEntries)
                .Contains(MeetPolicy.CreatedScope, StringComparer.Ordinal);
        context.HttpContext.Items[ProposalKey] = new Proposal(subject, context.RefreshToken, granted);
    }
    internal static string? Complete(HttpContext context, string id)
    {
        AccountWire.Need(context.Items.Remove(ProposalKey, out object? value) && value is Proposal, "providerIdentityInvalid", 502);
        var proposal = (Proposal)value!;
        return context.RequestServices.GetRequiredService<MeetAuthorizationTransactions>().CompleteBrowser(
            id, proposal.Subject, proposal.RefreshToken, proposal.ScopeGranted);
    }
    internal static void Map(WebApplication app)
    {
        // Decryption/format/writer failures prevent the service from advertising authorization readiness.
        _ = app.Services.GetRequiredService<MeetGrantRepository>();
        app.MapGet("/auth/google/meet-start", async context =>
        {
            AccountWire.Need(context.Request.Query.Count == 1 && context.Request.Query["id"].Count == 1);
            string id = context.Request.Query["id"].ToString();
            context.RequestServices.GetRequiredService<MeetAuthorizationTransactions>().BeginBrowser(id);
            OAuthChallengeProperties properties = new() { RedirectUri = "/auth/google/complete", Scope = ["openid", "profile", "email", MeetPolicy.CreatedScope] };
            properties.Items[AccountModule.TransactionKey] = id; properties.Items[PurposeKey] = "meet";
            await context.ChallengeAsync(AccountModule.Google, properties);
        }).RequireRateLimiting("account");
        app.MapPost("/v2/rooms/{roomId}/call-authorizations", async context =>
        {
            var (rooms, principal, room, member, run) = AuthenticatedRoomModule.Member(context);
            JsonElement body = await AuthenticatedRoomModule.Read(context, "challenge");
            RoomMemberAccess access = rooms.Access(principal, room, member, run, owner: true);
            await AccountModule.Reply(context, context.RequestServices.GetRequiredService<MeetAuthorizationTransactions>().Start(
                access, Text(body, "challenge")));
        }).RequireRateLimiting("account");
        var group = app.MapGroup("/v2/call-authorizations/{id}").RequireRateLimiting("account");
        foreach (string action in new[] { "poll", "verify", "confirm", "cancel" })
        {
            group.MapPost("/" + action, async context =>
            {
                AccountPrincipal principal = context.RequestServices.GetRequiredService<AccountRepository>().Authorize(AccountModule.Bearer(context));
                string[] fields = action switch { "verify" => ["proof", "browserProof"], "confirm" => ["proof", "browserProof", "requestId"], _ => ["proof"] };
                JsonElement body = await AuthenticatedRoomModule.Read(context, fields);
                string id = (string)context.Request.RouteValues["id"]!;
                string proof = Text(body, "proof");
                var transactions = context.RequestServices.GetRequiredService<MeetAuthorizationTransactions>();
                MeetAuthorizationStatus result = action switch
                {
                    "poll" => transactions.Poll(principal, id, proof),
                    "verify" => transactions.Verify(principal, id, proof, Text(body, "browserProof")),
                    "confirm" => transactions.Confirm(principal, id, proof, Text(body, "requestId"), Text(body, "browserProof")),
                    _ => transactions.Cancel(principal, id, proof)
                };
                await AccountModule.Reply(context, result);
            });
        }
    }
    private static string Text(JsonElement body, string key)
    {
        AccountWire.Need(body.GetProperty(key).ValueKind == JsonValueKind.String);
        return body.GetProperty(key).GetString()!;
    }
}

internal sealed class MeetAuthorizationCleanup(MeetAuthorizationTransactions transactions) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using PeriodicTimer timer = new(TimeSpan.FromSeconds(15));
        while (await timer.WaitForNextTickAsync(stoppingToken)) transactions.Sweep();
    }
}
