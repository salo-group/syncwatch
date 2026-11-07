using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text.Json;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.OAuth;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.RateLimiting;
using SyncWatch.RoomMetadata.Calls;

namespace SyncWatch.RoomMetadata.Accounts;

public static class AccountModule
{
    internal const string Google = "google-account";
    internal const string TransactionKey = "syncwatch.transaction";
    private sealed record StartBody(int V, string Challenge);
    private sealed record ProofBody(int V, string Proof, string? RequestId = null, string? BrowserProof = null);
    private sealed record RefreshBody(int V, string RefreshToken, string RequestId);

    public static void Configure(WebApplicationBuilder builder, AccountOptions options)
    {
        // This deployment deliberately requires an OS-protected server key ring.
        // Other hosts need an explicitly configured certificate/HSM protector before enabling accounts.
        AccountWire.Need(OperatingSystem.IsWindows(), "accountKeyProtectionRequired");
        string keys = Path.Combine(options.Directory, "keys");
        IDataProtectionBuilder protection = builder.Services.AddDataProtection().SetApplicationName("SyncWatch.Accounts.v2")
            .PersistKeysToFileSystem(new DirectoryInfo(keys));
        if (OperatingSystem.IsWindows()) protection.ProtectKeysWithDpapi();
        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton(provider => new AccountRepository(options.Directory, provider.GetRequiredService<IDataProtectionProvider>()));
        builder.Services.AddSingleton(provider => new SignInTransactions(provider.GetRequiredService<AccountRepository>(), options.PublicOrigin));
        builder.Services.AddHostedService<AccountCleanup>();
        builder.Services.AddRateLimiter(limiter =>
        {
            limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            limiter.OnRejected = async (context, cancellation) =>
            {
                var response = context.HttpContext.Response;
                response.StatusCode = StatusCodes.Status429TooManyRequests;
                if (context.Lease.TryGetMetadata(MetadataName.RetryAfter, out TimeSpan retry))
                    response.Headers.RetryAfter = Math.Clamp(Math.Ceiling(retry.TotalSeconds), 1, 60).ToString(System.Globalization.CultureInfo.InvariantCulture);
                await response.WriteAsJsonAsync(new { v = 2, error = new { code = "queueFull" } }, AccountWire.Json, cancellation);
            };
            limiter.AddPolicy("account", context => RateLimitPartition.GetFixedWindowLimiter(
                context.Connection.RemoteIpAddress?.ToString() ?? "unknown", _ => new FixedWindowRateLimiterOptions
                { PermitLimit = 120, Window = TimeSpan.FromMinutes(1), QueueLimit = 0 }));
        });
        builder.Services.AddAuthentication().AddCookie("account-browser").AddOAuth(Google, oauth =>
        {
            oauth.ClientId = options.ClientId; oauth.ClientSecret = options.ClientSecret;
            oauth.AuthorizationEndpoint = "https://accounts.google.com/o/oauth2/v2/auth";
            oauth.TokenEndpoint = "https://oauth2.googleapis.com/token";
            oauth.UserInformationEndpoint = "https://openidconnect.googleapis.com/v1/userinfo";
            oauth.CallbackPath = "/auth/google/callback"; oauth.SignInScheme = "account-browser";
            oauth.UsePkce = true; oauth.SaveTokens = false;
            oauth.Scope.Clear(); oauth.Scope.Add("openid"); oauth.Scope.Add("profile"); oauth.Scope.Add("email");
            oauth.RemoteAuthenticationTimeout = TimeSpan.FromMinutes(5);
            oauth.CorrelationCookie.HttpOnly = true; oauth.CorrelationCookie.SameSite = SameSiteMode.Lax;
            oauth.CorrelationCookie.SecurePolicy = options.PublicOrigin.Scheme == "https" ? CookieSecurePolicy.Always : CookieSecurePolicy.SameAsRequest;
            oauth.BackchannelTimeout = TimeSpan.FromSeconds(20);
            oauth.BackchannelHttpHandler = new SocketsHttpHandler { AllowAutoRedirect = false, UseCookies = false, ConnectTimeout = TimeSpan.FromSeconds(10) };
            oauth.Events.OnRedirectToAuthorizationEndpoint = context =>
            {
                context.Response.Redirect(MeetAuthorizationModule.AuthorizationUri(context.RedirectUri, context.Properties));
                return Task.CompletedTask;
            };
            oauth.Events.OnCreatingTicket = async context =>
            {
                // Use the reviewed OAuth code/PKCE/correlation handler and authenticated Google UserInfo.
                // An id_token is never decoded, accepted, forwarded or treated as identity evidence.
                using HttpRequestMessage request = new(HttpMethod.Get, context.Options.UserInformationEndpoint);
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", context.AccessToken);
                using HttpResponseMessage response = await context.Backchannel.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, context.HttpContext.RequestAborted);
                AccountWire.Need(response.IsSuccessStatusCode && response.Content.Headers.ContentLength is not > 16384, "providerIdentityInvalid", 502);
                using Stream stream = await response.Content.ReadAsStreamAsync(context.HttpContext.RequestAborted);
                byte[] bytes = new byte[16385];
                int count = await stream.ReadAtLeastAsync(bytes, bytes.Length, throwOnEndOfStream: false, context.HttpContext.RequestAborted);
                AccountWire.Need(count <= 16384, "providerIdentityInvalid", 502);
                using JsonDocument json = JsonDocument.Parse(bytes.AsMemory(0, count));
                JsonElement user = json.RootElement;
                string subject = user.GetProperty("sub").GetString()!, email = user.GetProperty("email").GetString()!;
                string name = user.TryGetProperty("name", out JsonElement display) ? display.GetString() ?? email : email;
                AccountWire.Need(user.GetProperty("email_verified").ValueKind == JsonValueKind.True
                    && AccountWire.Text(subject, 255) && AccountWire.Text(name, 128) && AccountWire.Text(email, 320), "providerIdentityInvalid", 502);
                context.Identity!.AddClaim(new(ClaimTypes.NameIdentifier, subject));
                context.Identity.AddClaim(new(ClaimTypes.Name, name)); context.Identity.AddClaim(new(ClaimTypes.Email, email));
                MeetAuthorizationModule.Propose(context, subject, user);
            };
            oauth.Events.OnTicketReceived = async context =>
            {
                string id = context.Properties!.Items[TransactionKey]!;
                if (MeetAuthorizationModule.Requested(context.Properties))
                {
                    string? proof = MeetAuthorizationModule.Complete(context.HttpContext, id);
                    context.HandleResponse(); await BrowserResult(context.HttpContext, proof is not null, proof, meet: true);
                    return;
                }
                ClaimsPrincipal principal = context.Principal!;
                string browserProof = context.HttpContext.RequestServices.GetRequiredService<SignInTransactions>().CompleteBrowser(id,
                    principal.FindFirstValue(ClaimTypes.NameIdentifier)!, principal.FindFirstValue(ClaimTypes.Name)!,
                    principal.FindFirstValue(ClaimTypes.Email)!);
                context.HandleResponse();
                await BrowserResult(context.HttpContext, success: true, browserProof);
            };
            oauth.Events.OnRemoteFailure = async context =>
            {
                if (context.Properties?.Items.TryGetValue(TransactionKey, out string? id) == true && id is not null)
                {
                    if (MeetAuthorizationModule.Requested(context.Properties)) context.HttpContext.RequestServices.GetService<MeetAuthorizationTransactions>()?.FailBrowser(id);
                    else context.HttpContext.RequestServices.GetRequiredService<SignInTransactions>().FailBrowser(id);
                }
                context.HandleResponse(); await BrowserResult(context.HttpContext, success: false, meet: MeetAuthorizationModule.Requested(context.Properties));
            };
        });
    }

    public static void Map(WebApplication app, AccountOptions options)
    {
        // Fail at startup for a locked/corrupt/unreadable store, before accepting sign-in requests.
        _ = app.Services.GetRequiredService<AccountRepository>();
        app.UseRateLimiter();
        app.UseAuthentication();
        app.MapGet("/auth/google/start", async context =>
        {
            AccountWire.Need(context.Request.Query.Count == 1 && context.Request.Query["id"].Count == 1);
            string id = context.Request.Query["id"].ToString();
            context.RequestServices.GetRequiredService<SignInTransactions>().BeginBrowser(id);
            AuthenticationProperties properties = new() { RedirectUri = "/auth/google/complete" };
            properties.Items[TransactionKey] = id;
            await context.ChallengeAsync(Google, properties);
        }).RequireRateLimiting("account");
        RouteGroupBuilder group = app.MapGroup("/v2/account").RequireRateLimiting("account");
        group.MapPost("/sign-ins", async context =>
        {
            StartBody body = await Read<StartBody>(context); Version(body.V);
            await Reply(context, context.RequestServices.GetRequiredService<SignInTransactions>().Start(body.Challenge));
        });
        foreach (string action in new[] { "poll", "verify", "confirm", "cancel" })
        {
            group.MapPost("/sign-ins/{id}/" + action, async context =>
            {
                ProofBody body = await Read<ProofBody>(context); Version(body.V);
                if (action is "poll" or "cancel") AccountWire.Need(body.RequestId is null && body.BrowserProof is null);
                if (action == "verify") AccountWire.Need(body.RequestId is null);
                SignInTransactions transactions = context.RequestServices.GetRequiredService<SignInTransactions>();
                string id = context.Request.RouteValues["id"]!.ToString()!;
                if (action == "cancel") { transactions.Cancel(id, body.Proof); await Reply(context, new SignInStatus("cancelled")); }
                else if (action == "verify") await Reply(context, transactions.VerifyBrowser(id, body.Proof, body.BrowserProof ?? ""));
                else await Reply(context, action == "poll" ? transactions.Poll(id, body.Proof)
                    : transactions.Confirm(id, body.Proof, body.RequestId ?? "", body.BrowserProof ?? ""));
            });
        }
        group.MapGet("/me", context => Reply(context, new { account = context.RequestServices.GetRequiredService<AccountRepository>().Authenticate(Bearer(context)) }));
        group.MapPost("/refresh", async context =>
        {
            RefreshBody body = await Read<RefreshBody>(context); Version(body.V);
            await Reply(context, new { session = context.RequestServices.GetRequiredService<AccountRepository>().Refresh(body.RefreshToken, body.RequestId) });
        });
        group.MapPost("/logout", async context =>
        {
            AccountWire.Need(context.Features.Get<Microsoft.AspNetCore.Http.Features.IHttpRequestBodyDetectionFeature>()?.CanHaveBody != true);
            context.RequestServices.GetRequiredService<AccountRepository>().Logout(Bearer(context));
            await Reply(context, new { status = "signedOut" });
        });
    }

    public static void ValidateOrigin(HttpContext context, AccountOptions options)
    {
        AccountWire.Need(string.Equals(context.Request.Host.Value, options.PublicOrigin.Authority, StringComparison.OrdinalIgnoreCase)
            && context.Request.Scheme == options.PublicOrigin.Scheme && !context.Request.PathBase.HasValue, "accountOriginInvalid", 403);
        context.Response.Headers.ContentSecurityPolicy = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
        context.Response.Headers["Referrer-Policy"] = "no-referrer";
        context.Response.Headers.XContentTypeOptions = "nosniff";
        context.Response.Headers.XFrameOptions = "DENY";
    }
    private static void Version(int version) => AccountWire.Need(version == 2, "unsupportedVersion");
    internal static string Bearer(HttpContext context)
    {
        string value = context.Request.Headers.Authorization.ToString();
        AccountWire.Need(value.StartsWith("Bearer ", StringComparison.Ordinal) && AccountWire.Token(value[7..]), "unauthorized", 401);
        return value[7..];
    }
    private static async Task<T> Read<T>(HttpContext context)
    {
        AccountWire.Need(context.Request.ContentType?.Split(';')[0].Trim() == "application/json");
        byte[] bytes = new byte[4097];
        int count = await context.Request.Body.ReadAtLeastAsync(bytes, bytes.Length, throwOnEndOfStream: false, context.RequestAborted);
        AccountWire.Need(count <= 4096, "frameTooLarge", 413);
        try
        {
            using JsonDocument json = JsonDocument.Parse(bytes.AsMemory(0, count));
            AccountWire.Need(json.RootElement.ValueKind == JsonValueKind.Object);
            string[] names = json.RootElement.EnumerateObject().Select(property => property.Name).ToArray();
            AccountWire.Need(names.Distinct(StringComparer.Ordinal).Count() == names.Length);
            return json.RootElement.Deserialize<T>(AccountWire.Json) ?? throw new AccountException("malformed");
        }
        catch (JsonException) { throw new AccountException("malformed"); }
    }
    internal static Task Reply(HttpContext context, object value) => context.Response.WriteAsJsonAsync(new { v = 2, result = value }, AccountWire.Json, context.RequestAborted);
    private static async Task BrowserResult(HttpContext context, bool success, string? browserProof = null, bool meet = false)
    {
        context.Response.ContentType = "text/html; charset=utf-8";
        context.Response.StatusCode = success ? 200 : 400;
        await context.Response.WriteAsync("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>SyncWatch</title><h1>SyncWatch</h1><p>"
            + (success ? (meet ? "Return to the SyncWatch call authorization window. Enter this one-time code there, check the account and confirm permission to create calls. "
                : "Return to the SyncWatch window where you started sign-in. Enter this one-time confirmation code there, check the account, and confirm. ")
                + "Do not send this code to another person or enter it into another website.</p><code>"
                + System.Net.WebUtility.HtmlEncode(browserProof) + "</code><p>You can close this tab after confirming in SyncWatch."
                : meet ? "Call authorization did not finish. Return to SyncWatch to try again." : "Sign-in did not finish. Return to SyncWatch to try again.") + "</p></html>", context.RequestAborted);
    }
}

internal sealed class AccountCleanup(AccountRepository repository, SignInTransactions transactions) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using PeriodicTimer timer = new(TimeSpan.FromSeconds(15));
        while (await timer.WaitForNextTickAsync(stoppingToken)) { transactions.Sweep(); repository.Sweep(); }
    }
}
