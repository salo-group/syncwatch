using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.Extensions.DependencyInjection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;

// Test-assembly-only browser/provider boundary. No route or synthetic identity enters the service executable.
internal sealed class NativeMeetBrowserFixture : IDisposable
{
    private sealed record Identity(string AccountId, string Subject, byte[] TokenHash);
    private readonly object gate = new();
    private readonly List<Identity> identities = [];
    private readonly HttpClient browser = new(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
    { Timeout = TimeSpan.FromSeconds(10) };

    internal NativeMeetBrowserFixture(Microsoft.AspNetCore.Builder.WebApplication app, Uri origin, Action? expirePermission = null)
    {
        app.MapPost("/__test/meet-browser", async context =>
        {
            string authorization = context.Request.Headers.Authorization.ToString();
            AccountPrincipal? principal = null;
            if (authorization.Length > 0)
            {
                Wire.Need(authorization.StartsWith("Bearer ", StringComparison.Ordinal), "unauthorized", 401);
                principal = app.Services.GetRequiredService<AccountRepository>().Authorize(authorization[7..]);
            }
            byte[] bytes = new byte[4097];
            int count = await context.Request.Body.ReadAtLeastAsync(bytes, bytes.Length, false, context.RequestAborted);
            Wire.Need(count <= 4096);
            JsonElement body = Wire.Parse(bytes.AsMemory(0, count));
            Wire.Fields(body, "v", "transactionId", "scenario", "fixtureToken");
            Wire.Need(Wire.Integer(body, "v", 2) == 2);
            string id = Wire.String(body, "transactionId"), scenario = Wire.String(body, "scenario");
            Identity? identity;
            // Application checks have no API for extracting device bearers. Their private fixture token
            // identifies only this test server's fixed synthetic provider subject, never a production identity.
            lock (gate) identity = identities.SingleOrDefault(value => (principal is null || value.AccountId == principal.AccountId)
                && Wire.SecretMatches(Wire.String(body, "fixtureToken"), value.TokenHash));
            Wire.Need(identity is not null && scenario is "accept" or "wrongAccount" or "missingScope" or "reuse" or "expire", "forbidden", 403);
            Wire.Need(scenario != "expire" || expirePermission is not null, "forbidden", 403);

            // Exercise the real per-challenge OAuth adapter without following the redirect to Google.
            using var redirect = await browser.GetAsync(new Uri(origin, "/auth/google/meet-start?id=" + Uri.EscapeDataString(id)), context.RequestAborted);
            Uri? destination = redirect.Headers.Location;
            Wire.Need(redirect.StatusCode == HttpStatusCode.Redirect && destination is not null
                && destination.Scheme == "https" && destination.Host == "accounts.google.com" && destination.AbsolutePath == "/o/oauth2/v2/auth");
            var query = QueryHelpers.ParseQuery(destination!.Query);
            Wire.Need(query["scope"].ToString().Split(' ').ToHashSet(StringComparer.Ordinal).SetEquals(
                ["openid", "profile", "email", "https://www.googleapis.com/auth/meetings.space.created"]));
            Wire.Need(query["access_type"] == "offline" && query["prompt"] == "consent select_account"
                && query["include_granted_scopes"] == "true" && query["code_challenge_method"] == "S256");
            string? code = app.Services.GetRequiredService<MeetAuthorizationTransactions>().CompleteBrowser(id,
                scenario == "wrongAccount" ? identity!.Subject + "-other" : identity!.Subject,
                scenario == "reuse" ? null : "synthetic-meet-refresh-" + Guid.NewGuid().ToString("N"), scenario != "missingScope");
            if (scenario == "expire") expirePermission!();
            context.Response.ContentType = "application/json";
            await JsonSerializer.SerializeAsync(context.Response.Body, new { v = 2, result = new { browserProof = code } }, Wire.Json, context.RequestAborted);
        });
    }
    internal void Add(string accountId, string subject, string token)
    { lock (gate) identities.Add(new(accountId, subject, Wire.Hash(token))); }
    internal async Task CheckIdentityOnlyChallenge(WebApplication app, Uri origin)
    {
        static string Base64(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        string proof = Base64(RandomNumberGenerator.GetBytes(32));
        var transactions = app.Services.GetRequiredService<SignInTransactions>();
        var start = transactions.Start(Base64(SHA256.HashData(Encoding.ASCII.GetBytes(proof))));
        try
        {
            using var redirect = await browser.GetAsync(start.BrowserUri);
            Uri? destination = redirect.Headers.Location;
            Wire.Need(redirect.StatusCode == HttpStatusCode.Redirect && destination is not null && destination.Host == "accounts.google.com");
            var query = QueryHelpers.ParseQuery(destination!.Query);
            Wire.Need(query["scope"].ToString().Split(' ').ToHashSet(StringComparer.Ordinal).SetEquals(["openid", "profile", "email"]));
            Wire.Need(!query.ContainsKey("access_type") && !query.ContainsKey("include_granted_scopes")
                && query["code_challenge_method"] == "S256" && query["redirect_uri"] == new Uri(origin, "/auth/google/callback").AbsoluteUri);
        }
        finally { transactions.Cancel(start.TransactionId, proof); }
        Console.WriteLine("SYNCWATCH_IDENTITY_ONLY_OAUTH_SCOPE_OK");
    }
    public void Dispose() => browser.Dispose();
}
