using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

internal static class SyncAuthorityChecks
{
    internal static void Run()
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-sync-authority-" + Guid.NewGuid().ToString("N"));
        try
        {
            using var accounts = new AccountRepository(directory, new EphemeralDataProtectionProvider());
            DeviceSession device = accounts.SignIn("owner", "Owner", "owner@example.invalid");
            AccountPrincipal principal = accounts.Authorize(device.AccessToken);
            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999);
            AuthenticatedAdmission room = rooms.Create(principal, "Owner", "owner-run", "create-1");
            using RoomStream stream = rooms.Attach(principal, room.RoomId, room.MemberId, "owner-run", null, null);
            var authority = new SyncAdmissionAuthority(rooms, "127.0.0.1", 8999);
            MemberBinding member = room.Bootstrap.Snapshot.Members.Single();
            SyncAdmissionTicket ticket = rooms.Synchronize(principal, room.RoomId, room.MemberId, "owner-run");
            SyncAuthorityLease lease = authority.Consume(ticket.Ticket, "owner-run", member.SyncplayUsername, room.Bootstrap.Connection.Room);
            Require(lease.ExpiresIn == 5 && authority.Updates().Single().Lease == lease);
            authority.Release("unknown-lease"); Require(authority.Updates().Single().Lease == lease);
            DeviceSession rotated = accounts.Refresh(device.RefreshToken, new string('r', 43));
            Require(authority.Updates().Single().Lease == lease);
            authority.Release(lease.Id); Require(authority.Updates().Length == 0);
            ticket = rooms.Synchronize(principal, room.RoomId, room.MemberId, "owner-run");
            lease = authority.Consume(ticket.Ticket, "owner-run", member.SyncplayUsername, room.Bootstrap.Connection.Room);
            accounts.Logout(rotated.RefreshToken);
            SyncAuthorityUpdate revoked = authority.Updates().Single();
            Require(revoked.Id == lease.Id && revoked.Lease is null && authority.Updates().Length == 0 && stream.Stopped.IsCancellationRequested);
            Console.WriteLine("Sync authority checks passed: scoped transport leases, rotation, exact release and logout revocation");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true); }
        string? runtime = Environment.GetEnvironmentVariable("SYNCWATCH_ADMISSION_RUNTIME");
        if (runtime is null) Console.WriteLine("SKIP real private Syncplay host: SYNCWATCH_ADMISSION_RUNTIME is not set");
        else Host(runtime).GetAwaiter().GetResult();
    }

    private static async Task Host(string runtime)
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-sync-host-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        using ECDsa rootKey = ECDsa.Create(ECCurve.NamedCurves.nistP256), leafKey = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var rootRequest = new CertificateRequest("CN=SyncWatch owned test root", rootKey, HashAlgorithmName.SHA256);
        rootRequest.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
        rootRequest.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        using X509Certificate2 root = rootRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(1));
        var request = new CertificateRequest("CN=SyncWatch owned loopback", leafKey, HashAlgorithmName.SHA256);
        var alternative = new SubjectAlternativeNameBuilder(); alternative.AddIpAddress(IPAddress.Loopback);
        request.CertificateExtensions.Add(alternative.Build());
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, true));
        using X509Certificate2 leaf = request.Create(root, DateTimeOffset.UtcNow.AddHours(-1), DateTimeOffset.UtcNow.AddHours(1), RandomNumberGenerator.GetBytes(16));
        try
        {
            await File.WriteAllTextAsync(Path.Combine(directory, "privkey.pem"), leafKey.ExportPkcs8PrivateKeyPem());
            await File.WriteAllTextAsync(Path.Combine(directory, "cert.pem"), leaf.ExportCertificatePem());
            await File.WriteAllTextAsync(Path.Combine(directory, "chain.pem"), root.ExportCertificatePem());
            using var reservation = new TcpListener(IPAddress.Loopback, 0); reservation.Start();
            int port = ((IPEndPoint)reservation.LocalEndpoint).Port; reservation.Stop();
            using var accounts = new AccountRepository(Path.Combine(directory, "accounts"), new EphemeralDataProtectionProvider());
            DeviceSession session = accounts.SignIn("host-owner", "Host owner", "host@example.invalid");
            AccountPrincipal principal = accounts.Authorize(session.AccessToken);
            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", port);
            AuthenticatedAdmission room = rooms.Create(principal, "Host owner", "host-run", "host-create");
            using RoomStream events = rooms.Attach(principal, room.RoomId, room.MemberId, "host-run", null, null);
            var authority = new SyncAdmissionAuthority(rooms, "127.0.0.1", port);
            await using SyncServerProcess owner = await SyncServerProcess.StartAsync(new(runtime, directory, "127.0.0.1", port), authority);
            using var tcp = new TcpClient(); await tcp.ConnectAsync(IPAddress.Loopback, port);
            NetworkStream network = tcp.GetStream();
            await Send(network, new { TLS = new { startTLS = "send" } });
            Require((await Receive(network)).GetProperty("TLS").GetProperty("startTLS").GetString() == "true");
            using var secured = new SslStream(network, leaveInnerStreamOpen: true, (_, certificate, _, errors) =>
            {
                if (certificate is null || (errors & ~SslPolicyErrors.RemoteCertificateChainErrors) != 0) return false;
                using var chain = new X509Chain();
                chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust; chain.ChainPolicy.CustomTrustStore.Add(root);
                chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                using var presented = new X509Certificate2(certificate);
                return chain.Build(presented);
            });
            using CancellationTokenSource deadline = new(TimeSpan.FromSeconds(20));
            await secured.AuthenticateAsClientAsync(new SslClientAuthenticationOptions { TargetHost = "127.0.0.1", EnabledSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13 }, deadline.Token);
            MemberBinding member = room.Bootstrap.Snapshot.Members.Single();
            SyncAdmissionTicket ticket = rooms.Synchronize(principal, room.RoomId, room.MemberId, "host-run");
            await Send(secured, new { Admission = new { ticket = ticket.Ticket, clientRunId = "host-run", username = member.SyncplayUsername, room = room.Bootstrap.Connection.Room } });
            Require((await Receive(secured)).GetProperty("Admission").GetProperty("accepted").GetBoolean());
            await Send(secured, new { Hello = new { username = member.SyncplayUsername, room = new { name = room.Bootstrap.Connection.Room }, version = "1.2.255", features = new { uiMode = "cli" } } });
            JsonElement hello = await Until(secured, "Hello");
            Require(hello.GetProperty("username").GetString() == member.SyncplayUsername);
            await Task.Delay(TimeSpan.FromSeconds(6), deadline.Token);
            await Send(secured, new { List = new { } });
            Require((await Until(secured, "List")).EnumerateObject().Single().Name == room.Bootstrap.Connection.Room);
            accounts.Logout(session.RefreshToken);
            bool ended = false;
            try { while (true) _ = await Receive(secured); }
            catch (EndOfStreamException) { ended = true; }
            catch (IOException) { ended = true; }
            Require(ended && events.Stopped.IsCancellationRequested && !owner.Completion.IsCompleted);
            using var pending = new TcpClient(); await pending.ConnectAsync(IPAddress.Loopback, port);
            await owner.DisposeAsync(); await owner.Completion;
            bool pendingClosed = false;
            try { pendingClosed = await pending.GetStream().ReadAsync(new byte[1], deadline.Token) == 0; }
            catch (IOException error) when (error.InnerException is SocketException socket && socket.SocketErrorCode is SocketError.ConnectionReset or SocketError.ConnectionAborted) { pendingClosed = true; }
            Require(pendingClosed);
            string invalid = Path.Combine(directory, "missing-certificates"); Directory.CreateDirectory(invalid);
            bool badConfiguration = false;
            try { await using var unexpected = await SyncServerProcess.StartAsync(new(runtime, invalid, "127.0.0.1", port), authority); }
            catch (IOException) { badConfiguration = true; }
            Require(badConfiguration);
            using CancellationTokenSource cancelled = new(); cancelled.Cancel();
            bool startupCancelled = false;
            try { await using var unexpected = await SyncServerProcess.StartAsync(new(runtime, directory, "127.0.0.1", port), authority, cancelled.Token); }
            catch (OperationCanceledException) { startupCancelled = true; }
            Require(startupCancelled);
            Console.WriteLine("Real private Syncplay host passed: inherited pipes, account admission, renewal, logout disconnect, pending-socket close, invalid config/cancel and normal process cleanup");
        }
        finally { Directory.Delete(directory, recursive: true); }
    }

    private static async Task Send(Stream stream, object value)
    {
        using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(5));
        await stream.WriteAsync(JsonSerializer.SerializeToUtf8Bytes(value), timeout.Token);
        await stream.WriteAsync(new byte[] { (byte)'\r', (byte)'\n' }, timeout.Token); await stream.FlushAsync(timeout.Token);
    }
    private static async Task<JsonElement> Receive(Stream stream)
    {
        using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(5));
        using MemoryStream frame = new(); byte[] item = new byte[1];
        while (frame.Length < 16384)
        {
            if (await stream.ReadAsync(item, timeout.Token) == 0) throw new EndOfStreamException();
            if (item[0] == '\n') return Wire.Parse(frame.ToArray());
            frame.WriteByte(item[0]);
        }
        throw new InvalidOperationException("Private host test frame too large");
    }
    private static async Task<JsonElement> Until(Stream stream, string type)
    {
        for (int index = 0; index < 32; index++)
        {
            JsonElement value = await Receive(stream);
            if (value.TryGetProperty(type, out JsonElement result)) return result;
        }
        throw new InvalidOperationException("Private host test response missing");
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Sync authority assertion failed"); }
}
