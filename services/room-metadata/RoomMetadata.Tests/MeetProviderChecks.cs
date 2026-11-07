using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;

internal static class MeetProviderChecks
{
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
    private sealed class Transport : HttpMessageHandler
    {
        internal int Tokens, Reads, Creates, Ends;
        internal bool ConsumerDefaults;
        internal Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>? Token, Meet;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellation)
        {
            if (request.RequestUri!.AbsoluteUri == "https://oauth2.googleapis.com/token")
            {
                Interlocked.Increment(ref Tokens);
                string form = await request.Content!.ReadAsStringAsync(cancellation);
                Require(request.Method == HttpMethod.Post && form.Contains("grant_type=refresh_token", StringComparison.Ordinal)
                    && form.Contains("client_id=synthetic-client", StringComparison.Ordinal)
                    && form.Contains("client_secret=synthetic-secret", StringComparison.Ordinal));
                return Token is null ? Json("{\"access_token\":\"synthetic-access\",\"token_type\":\"Bearer\",\"expires_in\":3600}") : await Token(request, cancellation);
            }
            Require(request.RequestUri.Host == "meet.googleapis.com" && request.Headers.Authorization is { Scheme: "Bearer", Parameter: "synthetic-access" });
            if (request.Method == HttpMethod.Get) Interlocked.Increment(ref Reads);
            else if (request.RequestUri.AbsolutePath.EndsWith(":endActiveConference", StringComparison.Ordinal)) Interlocked.Increment(ref Ends);
            else
            {
                Require(request.Method == HttpMethod.Post && request.RequestUri.AbsolutePath == "/v2/spaces");
                using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
                if (ConsumerDefaults) Require(body.RootElement.ValueKind == JsonValueKind.Object && !body.RootElement.EnumerateObject().Any());
                else Require(body.RootElement.GetProperty("config").GetProperty("accessType").GetString() == "RESTRICTED"
                    && body.RootElement.GetProperty("config").GetProperty("entryPointAccess").GetString() == "ALL");
                Interlocked.Increment(ref Creates);
            }
            if (Meet is not null) return await Meet(request, cancellation);
            return Json(request.RequestUri.AbsolutePath.EndsWith(":endActiveConference", StringComparison.Ordinal) ? "{}" : Space);
        }
    }
    private const string Space = "{\"name\":\"spaces/owned-space\",\"meetingUri\":\"https://meet.google.com/abc-defg-hij\",\"config\":{\"accessType\":\"RESTRICTED\",\"entryPointAccess\":\"ALL\"}}";
    internal static async Task Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet provider protection requires Windows validation");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-provider-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var protection = DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")), builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Provider.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
            var clock = new Clock();
            using var accounts = new AccountRepository(root, protection, clock);
            using var grants = new MeetGrantRepository(accounts, root, protection);
            var session = accounts.SignIn("provider-owner", "Owner", "owner@example.invalid");
            var principal = accounts.Authorize(session.AccessToken);
            var options = new AccountOptions("synthetic-client", "synthetic-secret", new Uri("http://127.0.0.1:8788"), root);
            void Remember() => grants.Remember(principal, "provider-owner", "synthetic-refresh");
            var transport = new Transport { Token = (_, _) => Task.FromResult(Json("{\"access_token\":\"synthetic-access\",\"token_type\":\"Bearer\",\"expires_in\":180}")) };
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(transport), clock))
            {
                await Reject(() => provider.Create(principal.AccountId, default), "permissionRequired");
                Require(transport.Tokens == 0 && transport.Creates == 0);
                Remember();
                var reads = await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => provider.Read(principal.AccountId, "spaces/owned-space", default)));
                Require(transport.Tokens == 1 && reads.All(space => space.MeetUri == "https://meet.google.com/abc-defg-hij"));
                _ = await provider.Create(principal.AccountId, default);
                await provider.End(principal.AccountId, "spaces/owned-space", default);
                Require(transport.Creates == 1 && transport.Ends == 1 && transport.Tokens == 1);
                clock.Now += TimeSpan.FromSeconds(121);
                _ = await provider.Read(principal.AccountId, "spaces/owned-space", default);
                Require(transport.Tokens == 2);
                transport.Meet = (_, _) => Task.FromResult(Json("{\"error\":\"private-provider-detail\"}", HttpStatusCode.Unauthorized));
                await Reject(() => provider.Create(principal.AccountId, default), "permissionRequired");
                transport.Meet = null;
                _ = await provider.Read(principal.AccountId, "spaces/owned-space", default);
                Require(transport.Tokens == 3);
                transport.Meet = (_, _) => Task.FromResult(Json("{\"private\":\"provider failure\"}", HttpStatusCode.InternalServerError));
                await Reject(() => provider.Create(principal.AccountId, default), "providerUnavailable", ambiguous: true);
                Require(transport.Creates == 3); // One dispatch per invocation; ambiguous mutation never retries.
                transport.Meet = (_, _) => Task.FromResult(Json("{\"name\":7}"));
                await Reject(() => provider.Create(principal.AccountId, default), "providerResponseInvalid", ambiguous: true);
                transport.Meet = (_, _) => Task.FromResult(Json(Space.Replace("RESTRICTED", "OPEN", StringComparison.Ordinal)));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
                transport.Meet = (_, _) => Task.FromResult(Json(Space.Replace("RESTRICTED", "TRUSTED", StringComparison.Ordinal)));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
                transport.Meet = (_, _) => Task.FromResult(Json(Space.Replace("https://meet.google.com/", "https://evil.example/", StringComparison.Ordinal)));
                await Reject(() => provider.Create(principal.AccountId, default), "providerResponseInvalid", ambiguous: true);
                transport.Meet = (_, _) => Task.FromResult(Json("{\"name\":\"spaces/owned-space\",\"name\":\"spaces/other\"}"));
                await Reject(() => provider.Create(principal.AccountId, default), "providerResponseInvalid", ambiguous: true);
                transport.Meet = (_, _) => Task.FromResult(Json(new string('x', 32769)));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerResponseInvalid");
                await Reject(() => provider.Read(principal.AccountId, "spaces/../secrets", default), "providerResponseInvalid");
                Require(grants.Forget(principal));
                await Reject(() => provider.Create(principal.AccountId, default), "permissionRequired");
            }
            // A delayed refresh cannot attach its access token to a newer authorization version.
            Remember();
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var racing = new Transport { Token = async (_, cancel) => { entered.SetResult(true); await release.Task.WaitAsync(cancel); return Json("{\"access_token\":\"synthetic-access\",\"token_type\":\"Bearer\",\"expires_in\":3600,\"refresh_token\":\"rotated-old-grant\"}"); } };
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(racing), clock))
            {
                Task pending = provider.Create(principal.AccountId, default);
                await entered.Task.WaitAsync(TimeSpan.FromSeconds(5)); Remember(); release.SetResult(true);
                await Reject(() => pending, "permissionRequired"); Require(racing.Creates == 0);
            }
            var revoked = new Transport { Token = (_, _) => Task.FromResult(Json("{\"error\":\"invalid_grant\"}", HttpStatusCode.BadRequest)) };
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(revoked), clock))
            {
                await Reject(() => provider.Create(principal.AccountId, default), "permissionRequired");
                Require(!grants.HasAuthorization(principal) && revoked.Creates == 0);
            }
            foreach (string response in new[] { "{\"access_token\":\"synthetic-access\",\"token_type\":\"Bearer\",\"expires_in\":\"3600\"}",
                "{\"access_token\":\"synthetic-access\",\"token_type\":\"Bearer\",\"expires_in\":3600,\"scope\":\"openid\"}" })
            {
                Remember(); var invalid = new Transport { Token = (_, _) => Task.FromResult(Json(response)) };
                using var provider = new MeetGoogleProvider(grants, options, new HttpClient(invalid), clock);
                await Reject(() => provider.Create(principal.AccountId, default), "providerResponseInvalid"); Require(invalid.Creates == 0);
            }
            Remember();
            var reports = new List<(string Operation, int Status, string Reason)>();
            var denied = new Transport();
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(denied), clock,
                (operation, status, reason) => reports.Add((operation, status, reason))))
            {
                foreach (string reason in new[] { "SERVICE_DISABLED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "synthetic-access" })
                {
                    denied.Meet = (_, _) => Task.FromResult(Json(JsonSerializer.Serialize(new
                    { error = new { message = "synthetic-secret synthetic-refresh owner@example.invalid", details = new[] { new { reason } } } }), HttpStatusCode.Forbidden));
                    await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "permissionRequired");
                }
                Require(reports.SequenceEqual(new[] { ("read", 403, "SERVICE_DISABLED"), ("read", 403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT"), ("read", 403, "unspecified") }));
                Require(denied.Creates == 0 && denied.Reads == 3 && grants.HasAuthorization(principal));
            }
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(new Transport
            { Meet = (_, _) => Task.FromResult(Json("{}", HttpStatusCode.Forbidden)) }), clock,
                (_, _, _) => throw new IOException("Unavailable diagnostic sink")))
                await Reject(() => provider.Create(principal.AccountId, default), "permissionRequired");
            grants.Remember(principal, "provider-owner", "synthetic-refresh", managedDomain: false);
            var personal = new Transport { ConsumerDefaults = true };
            using (var provider = new MeetGoogleProvider(grants, options, new HttpClient(personal), clock))
            {
                _ = await provider.Create(principal.AccountId, default);
                _ = await provider.Read(principal.AccountId, "spaces/owned-space", default);
                string trusted = Space.Replace("RESTRICTED", "TRUSTED", StringComparison.Ordinal);
                personal.Meet = (_, _) => Task.FromResult(Json(trusted));
                _ = await provider.Read(principal.AccountId, "spaces/owned-space", default);
                personal.Meet = (_, _) => Task.FromResult(Json(trusted[..^1] + ",\"phoneAccess\":[],\"gatewaySipAccess\":[]}"));
                _ = await provider.Read(principal.AccountId, "spaces/owned-space", default);
                foreach (string field in new[] { "phoneAccess", "gatewaySipAccess" })
                    foreach (string entries in new[] { "[{}]", "null", "{}", "true" })
                    {
                        personal.Meet = (_, _) => Task.FromResult(Json(trusted[..^1] + ",\"" + field + "\":" + entries + "}"));
                        await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
                    }
                personal.Meet = (_, _) => Task.FromResult(Json(Space.Replace("RESTRICTED", "OPEN", StringComparison.Ordinal)));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
                personal.Meet = (_, _) => Task.FromResult(Json(Space.Replace("ALL", "CREATOR_APP_ONLY", StringComparison.Ordinal)));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
                personal.Meet = null;
                grants.Remember(principal, "provider-owner", "synthetic-refresh", managedDomain: true);
                personal.ConsumerDefaults = false;
                _ = await provider.Create(principal.AccountId, default);
                Require(personal.Creates == 2 && personal.Tokens == 2);
                personal.Meet = (_, _) => Task.FromResult(Json(trusted));
                await Reject(() => provider.Read(principal.AccountId, "spaces/owned-space", default), "providerPolicyInvalid");
            }
            foreach (string user in new[] { "{}", "{\"email\":\"person@company.example\"}" })
            { using var json = JsonDocument.Parse(user); Require(!MeetAuthorizationModule.HasManagedDomain(json.RootElement)); }
            using (var hosted = JsonDocument.Parse("{\"email\":\"person@gmail.com\",\"hd\":\"company.example\"}"))
                Require(MeetAuthorizationModule.HasManagedDomain(hosted.RootElement));
            foreach (string invalid in new[] { "null", "{\"hd\":null}", "{\"hd\":false}", "{\"hd\":\"\"}" })
            {
                using var json = JsonDocument.Parse(invalid);
                try { _ = MeetAuthorizationModule.HasManagedDomain(json.RootElement); throw new InvalidOperationException("Malformed hosted-domain claim accepted"); }
                catch (AccountException error) when (error.Code == "providerIdentityInvalid") { }
            }
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_PROVIDER_OK encrypted-grant/version binding, single-flight tokens, bounded responses, restricted policy, canonical URI and no ambiguous mutation retries");
    }
    private static HttpResponseMessage Json(string body, HttpStatusCode status = HttpStatusCode.OK) => new(status)
    { Content = new StringContent(body, Encoding.UTF8, "application/json") };
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet provider invariant"); }
    private static async Task Reject(Func<Task> action, string code, bool ambiguous = false)
    {
        try { await action(); throw new InvalidOperationException("Expected " + code); }
        catch (MeetProviderException error) when (error.Code == code && error.Ambiguous == ambiguous) { }
    }
}
