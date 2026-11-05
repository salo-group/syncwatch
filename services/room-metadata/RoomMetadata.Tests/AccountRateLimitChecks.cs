using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;

internal static class AccountRateLimitChecks
{
    internal static async Task Run()
    {
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-account-rate-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            using var reservation = new TcpListener(IPAddress.Loopback, 0);
            reservation.Start(); int port = ((IPEndPoint)reservation.LocalEndpoint).Port; reservation.Stop();
            var origin = new Uri("http://127.0.0.1:" + port);
            await using var app = RoomApplication.Build(["--urls", origin.AbsoluteUri], creationKey: Wire.Secret(),
                accounts: new("synthetic-rate-client", "synthetic-rate-secret", origin, root));
            var account = app.Services.GetRequiredService<AccountRepository>().SignIn("rate-owner", "Owner", "owner@example.invalid");
            await app.StartAsync();
            try
            {
                using var client = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
                { BaseAddress = origin, Timeout = TimeSpan.FromSeconds(5) };
                client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", account.AccessToken);
                for (int request = 0; request < 120; request++)
                {
                    using var accepted = await client.GetAsync("/v2/account/me");
                    Require(accepted.StatusCode == HttpStatusCode.OK);
                }
                using var limited = await client.GetAsync("/v2/account/me");
                Require(limited.StatusCode == HttpStatusCode.TooManyRequests && limited.Content.Headers.ContentType?.MediaType == "application/json");
                Require(limited.Headers.RetryAfter?.Delta is { TotalSeconds: >= 1 and <= 60 });
                string bytes = await limited.Content.ReadAsStringAsync();
                Require(!bytes.Contains(account.AccessToken, StringComparison.Ordinal));
                using var json = JsonDocument.Parse(bytes);
                Require(json.RootElement.EnumerateObject().Select(item => item.Name).ToHashSet(StringComparer.Ordinal).SetEquals(["v", "error"])
                    && json.RootElement.GetProperty("v").GetInt32() == 2 && json.RootElement.GetProperty("error").GetProperty("code").GetString() == "queueFull");
            }
            finally { await app.StopAsync(); }
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_ACCOUNT_RATE_LIMIT_OK real HTTP budget, typed v2 rejection, retry delay and no bearer echo");
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Account rate-limit wire invariant"); }
}
