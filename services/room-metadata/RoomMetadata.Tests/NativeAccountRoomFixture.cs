using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;

/// <summary>Owned integration host with synthetic provider subjects, never a production login path.</summary>
internal static class NativeAccountRoomFixture
{
    internal static async Task Run(string directory, int requestedPort = 0)
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Account protection requires Windows");
        if (requestedPort is < 0 or > 65535) throw new ArgumentOutOfRangeException(nameof(requestedPort));
        directory = Path.GetFullPath(directory);
        if (Directory.Exists(directory)) throw new InvalidOperationException("Fixture directory must be new");
        string runtime = Environment.GetEnvironmentVariable("SYNCWATCH_ADMISSION_RUNTIME") ?? throw new InvalidOperationException("Admission runtime is required");
        Directory.CreateDirectory(directory);
        string tls = Path.Combine(directory, "tls"); Directory.CreateDirectory(tls);
        using var root = SyncAuthorityChecks.CreateIdentity(tls);
        using var reservation = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, requestedPort);
        reservation.Start(); int port = ((System.Net.IPEndPoint)reservation.LocalEndpoint).Port; reservation.Stop();
        using var synchronization = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
        synchronization.Start(); int syncPort = ((System.Net.IPEndPoint)synchronization.LocalEndpoint).Port; synchronization.Stop();
        var origin = new Uri("http://127.0.0.1:" + port);
        await using var app = RoomApplication.Build(["--urls", origin.AbsoluteUri], accounts: new("owned-native-client", "owned-native-secret", origin,
            Path.Combine(directory, "accounts")), authority: new(runtime, tls, "127.0.0.1", "127.0.0.1", syncPort));
        var accounts = app.Services.GetRequiredService<AccountRepository>();
        await app.StartAsync();
        try
        {
            foreach (string platform in new[] { "macos", "windows", "linux" })
            {
                object Session(string role)
                {
                    DeviceSession session = accounts.SignIn("native-" + platform + "-" + role, role, role + "@example.invalid");
                    string requestId = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
                    return new { v = 1, service = origin.AbsoluteUri, accountId = session.Account.Id, refreshToken = session.RefreshToken, requestId };
                }
                await File.WriteAllTextAsync(Path.Combine(directory, platform + ".json"), JsonSerializer.Serialize(new { owner = Session("owner"), guest = Session("guest") }, Wire.Json));
            }
            await File.WriteAllTextAsync(Path.Combine(directory, "ready.json"), JsonSerializer.Serialize(new { port, syncPort, syntheticAccounts = true }));
            using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(20));
            while (!File.Exists(Path.Combine(directory, "stop"))) await Task.Delay(100, timeout.Token);
        }
        finally
        {
            await app.StopAsync();
            foreach (string platform in new[] { "macos", "windows", "linux" }) File.Delete(Path.Combine(directory, platform + ".json"));
        }
    }
}
