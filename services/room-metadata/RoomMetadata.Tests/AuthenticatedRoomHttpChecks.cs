using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Security;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Security.Authentication;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using SyncWatch.RoomMetadata;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

internal static class AuthenticatedRoomHttpChecks
{
    internal static async Task Run()
    {
        string? runtime = Environment.GetEnvironmentVariable("SYNCWATCH_ADMISSION_RUNTIME");
        if (runtime is null) { Console.WriteLine("SKIP authenticated room HTTP/TLS: SYNCWATCH_ADMISSION_RUNTIME is not set"); return; }
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-room-http-" + Guid.NewGuid().ToString("N"));
        string tls = Path.Combine(directory, "tls"); Directory.CreateDirectory(tls);
        using var root = SyncAuthorityChecks.CreateIdentity(tls);
        WebApplication? app = null;
        int originalExitCode = Environment.ExitCode;
        DateTime started = DateTime.UtcNow;
        List<object> observed = [];
        try
        {
            int port = Port(), syncPort = Port();
            var origin = new Uri("http://127.0.0.1:" + port);
            app = RoomApplication.Build(["--urls", origin.AbsoluteUri], accounts: new("owned-test-client", "owned-test-secret", origin,
                Path.Combine(directory, "accounts")), authority: new(runtime, tls, "127.0.0.1", "127.0.0.1", syncPort));
            var accounts = app.Services.GetRequiredService<AccountRepository>();
            DeviceSession owner = accounts.SignIn("http-owner", "Owner", "owner@example.invalid");
            DeviceSession guest = accounts.SignIn("http-guest", "Guest", "guest@example.invalid");
            DeviceSession sibling = accounts.SignIn("http-owner", "Owner", "owner@example.invalid");
            await app.StartAsync();
            using var http = new HttpClient { BaseAddress = origin, Timeout = TimeSpan.FromSeconds(5) };
            async Task<JsonElement> Request(HttpMethod method, string path, string? token = null,
                string? member = null, string? run = null, object? body = null, HttpStatusCode status = HttpStatusCode.OK, string? originHeader = null)
            {
                using var request = new HttpRequestMessage(method, path);
                if (token is not null) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                if (member is not null) request.Headers.Add("X-SyncWatch-Member", member);
                if (run is not null) request.Headers.Add("X-SyncWatch-Run", run);
                if (originHeader is not null) request.Headers.Add("Origin", originHeader);
                if (body is not null) request.Content = new StringContent(body is string raw ? raw : JsonSerializer.Serialize(body, Wire.Json), Encoding.UTF8, "application/json");
                using HttpResponseMessage response = await http.SendAsync(request);
                Require(response.StatusCode == status, "HTTP status " + path + ": " + (int)response.StatusCode);
                byte[] bytes = await response.Content.ReadAsByteArrayAsync();
                if (bytes.Length == 0) return default;
                using JsonDocument json = JsonDocument.Parse(bytes);
                JsonElement envelope = json.RootElement;
                Require(envelope.GetProperty("v").GetInt32() == 2, "Versioned authority envelope");
                observed.Add(new { schema = path.StartsWith("/v2/account", StringComparison.Ordinal) ? "account-response" : "room-access-response", value = envelope.Clone() });
                return envelope.GetProperty(status == HttpStatusCode.OK ? "result" : "error").Clone();
            }
            object create = new { v = 2, clientRunId = "http-owner-run", requestId = "http-create" };
            await Request(HttpMethod.Post, "/v2/rooms", body: create, status: HttpStatusCode.Unauthorized);
            await Request(HttpMethod.Post, "/v1/rooms", body: new { v = 1 }, status: HttpStatusCode.NotFound);
            await Request(HttpMethod.Post, "/v2/rooms", owner.AccessToken, body: new { v = 2, clientRunId = "x", requestId = "x", syncplayHost = "untrusted.example.invalid" }, status: HttpStatusCode.BadRequest);
            await Request(HttpMethod.Post, "/v2/rooms", owner.AccessToken, body: "{\"v\":2,\"v\":2,\"clientRunId\":\"x\",\"requestId\":\"x\"}", status: HttpStatusCode.BadRequest);
            await Request(HttpMethod.Post, "/v2/rooms", owner.AccessToken, body: create, status: HttpStatusCode.Forbidden, originHeader: "https://example.invalid");
            JsonElement created = await Request(HttpMethod.Post, "/v2/rooms", owner.AccessToken, body: create);
            AuthenticatedAdmission admission = created.Deserialize<AuthenticatedAdmission>(Wire.Json)!;
            AuthenticatedAdmission repeated = (await Request(HttpMethod.Post, "/v2/rooms", owner.AccessToken, body: create)).Deserialize<AuthenticatedAdmission>(Wire.Json)!;
            Require(repeated.MemberId == admission.MemberId && !created.GetRawText().Contains("token", StringComparison.OrdinalIgnoreCase), "Correlated create without legacy credential");
            string path = "/v2/rooms/" + admission.RoomId;
            string ownerMember = admission.MemberId;
            await Request(HttpMethod.Get, path + "/snapshot", sibling.AccessToken, ownerMember, "http-owner-run", status: HttpStatusCode.Unauthorized);
            await Request(HttpMethod.Get, path + "/snapshot", owner.AccessToken, ownerMember, "foreign-run", status: HttpStatusCode.Unauthorized);
            RoomInvitation withdrawal = (await Request(HttpMethod.Post, path + "/invitation", owner.AccessToken, ownerMember, "http-owner-run")).Deserialize<RoomInvitation>(Wire.Json)!;
            await Request(HttpMethod.Post, path + "/admission-requests", guest.AccessToken, body: new { v = 2, clientRunId = "http-cancel-run", requestId = "http-cancel", invitation = withdrawal.Invitation });
            await Request(HttpMethod.Post, "/v2/room-admissions/http-cancel/cancel", sibling.AccessToken, body: new { v = 2, clientRunId = "http-cancel-run" }, status: HttpStatusCode.Unauthorized);
            await Request(HttpMethod.Post, "/v2/room-admissions/http-cancel/cancel", guest.AccessToken, body: new { v = 2, clientRunId = "foreign-run" }, status: HttpStatusCode.Unauthorized);
            await Request(HttpMethod.Post, "/v2/room-admissions/http-cancel/cancel", guest.AccessToken, body: new { v = 2, clientRunId = "http-cancel-run" });
            await Request(HttpMethod.Post, "/v2/room-admissions/http-cancel/cancel", guest.AccessToken, body: new { v = 2, clientRunId = "http-cancel-run" });
            JsonElement cancelled = await Request(HttpMethod.Get, "/v2/room-admissions/http-cancel", guest.AccessToken, run: "http-cancel-run");
            Require(cancelled.GetProperty("status").GetString() == "revoked", "Correlated cancellation");
            await Request(HttpMethod.Post, "/v2/room-admissions/http-cancel/accept", guest.AccessToken, body: new { v = 2, clientRunId = "http-cancel-run" }, status: HttpStatusCode.Forbidden);
            RoomInvitation invitation = (await Request(HttpMethod.Post, path + "/invitation", owner.AccessToken, ownerMember, "http-owner-run")).Deserialize<RoomInvitation>(Wire.Json)!;
            object join = new { v = 2, clientRunId = "http-guest-run", requestId = "http-join", invitation = invitation.Invitation };
            AdmissionRequest requested = (await Request(HttpMethod.Post, path + "/admission-requests", guest.AccessToken, body: join)).Deserialize<AdmissionRequest>(Wire.Json)!;
            Require(requested.Name == guest.Account.Name && requested.Email == guest.Account.Email && requested.AccountId == guest.Account.Id, "Provider-derived approval identity");
            string accept = "/v2/room-admissions/http-join/accept";
            await Request(HttpMethod.Post, accept, guest.AccessToken, body: new { v = 2, clientRunId = "http-guest-run" }, status: HttpStatusCode.Forbidden);
            AdmissionRequest[] pending = (await Request(HttpMethod.Get, path + "/pending", owner.AccessToken, ownerMember, "http-owner-run")).Deserialize<AdmissionRequest[]>(Wire.Json)!;
            Require(pending.Single().Id == requested.Id, "Opaque owner approval handle");
            await Request(HttpMethod.Post, path + "/decision", owner.AccessToken, ownerMember, "http-owner-run", new { v = 2, approvalId = requested.Id, approve = true });
            AuthenticatedAdmission joined = (await Request(HttpMethod.Post, accept, guest.AccessToken, body: new { v = 2, clientRunId = "http-guest-run" })).Deserialize<AuthenticatedAdmission>(Wire.Json)!;
            await Request(HttpMethod.Post, path + "/invitation", guest.AccessToken, joined.MemberId, "http-guest-run", status: HttpStatusCode.Forbidden);
            await Request(HttpMethod.Post, path + "/commands", guest.AccessToken, joined.MemberId, "http-guest-run", new
            {
                v = 1,
                id = "cmd-1",
                type = "call.linkChanged",
                clientRunId = "http-guest-run",
                roomEpoch = joined.Bootstrap.Snapshot.RoomEpoch,
                watchGeneration = 0,
                payload = new { meetUri = "https://meet.google.com/abc-defg-hij" }
            }, HttpStatusCode.Forbidden);
            using var ws = new ClientWebSocket();
            ws.Options.SetRequestHeader("Authorization", "Bearer " + guest.AccessToken);
            ws.Options.SetRequestHeader("X-SyncWatch-Member", joined.MemberId);
            ws.Options.SetRequestHeader("X-SyncWatch-Run", "http-guest-run");
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await ws.ConnectAsync(new Uri("ws://127.0.0.1:" + port + path + "/events"), deadline.Token);
            var buffer = new byte[Wire.MaximumBytes];
            WebSocketReceiveResult bootstrap = await ws.ReceiveAsync(buffer, deadline.Token);
            Require(bootstrap.MessageType == WebSocketMessageType.Text && bootstrap.EndOfMessage, "Real scoped metadata stream");
            SyncAdmissionTicket ticket = (await Request(HttpMethod.Post, path + "/sync-ticket", guest.AccessToken, joined.MemberId, "http-guest-run")).Deserialize<SyncAdmissionTicket>(Wire.Json)!;
            using var tcp = new TcpClient(); await tcp.ConnectAsync(IPAddress.Loopback, syncPort, deadline.Token);
            NetworkStream network = tcp.GetStream();
            await SyncAuthorityChecks.Send(network, new { TLS = new { startTLS = "send" } });
            Require((await SyncAuthorityChecks.Receive(network)).GetProperty("TLS").GetProperty("startTLS").GetString() == "true", "TLS required");
            using var secured = new SslStream(network, leaveInnerStreamOpen: true, SyncAuthorityChecks.Trust(root));
            await secured.AuthenticateAsClientAsync(new SslClientAuthenticationOptions { TargetHost = "127.0.0.1", EnabledSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13 }, deadline.Token);
            MemberBinding member = joined.Bootstrap.Snapshot.Members.Single(value => value.MemberId == joined.MemberId);
            await SyncAuthorityChecks.Send(secured, new { Admission = new { ticket = ticket.Ticket, clientRunId = "http-guest-run", username = member.SyncplayUsername, room = joined.Bootstrap.Connection.Room } });
            Require((await SyncAuthorityChecks.Receive(secured)).GetProperty("Admission").GetProperty("accepted").GetBoolean(), "HTTP-issued ticket enforced by actual Syncplay host");
            await SyncAuthorityChecks.Send(secured, new { Hello = new { username = member.SyncplayUsername, room = new { name = joined.Bootstrap.Connection.Room }, version = "1.2.255", features = new { uiMode = "cli" } } });
            Require((await SyncAuthorityChecks.Until(secured, "Hello")).GetProperty("username").GetString() == member.SyncplayUsername, "Scoped upstream Hello");
            await Request(HttpMethod.Post, "/v2/account/logout", guest.RefreshToken);
            await Request(HttpMethod.Get, path + "/snapshot", guest.AccessToken, joined.MemberId, "http-guest-run", status: HttpStatusCode.Unauthorized);
            bool closed = false;
            try { closed = (await ws.ReceiveAsync(buffer, deadline.Token)).MessageType == WebSocketMessageType.Close; }
            catch (WebSocketException) { closed = true; }
            Require(closed, "Logout closes metadata stream");
            closed = false;
            try { while (true) _ = await SyncAuthorityChecks.Receive(secured); }
            catch (IOException) { closed = true; }
            Require(closed, "Logout closes enforced Syncplay transport");
            await Request(HttpMethod.Get, path + "/snapshot", owner.AccessToken, ownerMember, "http-owner-run");
            await Request(HttpMethod.Post, path + "/leave", owner.AccessToken, ownerMember, "http-owner-run");
            await Request(HttpMethod.Get, path + "/snapshot", owner.AccessToken, ownerMember, "http-owner-run", status: HttpStatusCode.NotFound);
            using (Process child = await OwnedServer(runtime, started))
            {
                child.Kill(entireProcessTree: true);
                await child.WaitForExitAsync(deadline.Token);
                IHostApplicationLifetime lifetime = app.Services.GetRequiredService<IHostApplicationLifetime>();
                var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                using var registration = lifetime.ApplicationStopping.Register(() => stopped.TrySetResult());
                await stopped.Task.WaitAsync(TimeSpan.FromSeconds(5));
                Require(Environment.ExitCode == 1, "Unexpected authority exit fails the service closed");
            }
            string? output = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_ACCESS_CONTRACT_OUTPUT");
            if (output is not null) await File.WriteAllBytesAsync(output, JsonSerializer.SerializeToUtf8Bytes(observed, Wire.Json));
            Console.WriteLine("Authenticated HTTP/TLS room passed: no anonymous routes, scoped provider identity, approval, private metadata, real ticket/Hello, logout disconnect, owner close and unexpected-child fail-closed stop");
        }
        finally
        {
            try { if (app is not null) { try { await app.StopAsync(); } finally { await app.DisposeAsync(); } } }
            finally { Environment.ExitCode = originalExitCode; Directory.Delete(directory, recursive: true); }
        }
    }

    private static async Task<Process> OwnedServer(string runtime, DateTime started)
    {
        string command = "Get-CimInstance Win32_Process -Filter 'ParentProcessId = " + Environment.ProcessId
            + "' | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress";
        var options = new ProcessStartInfo("powershell")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (string arg in new[] { "-NoProfile", "-NonInteractive", "-EncodedCommand", Convert.ToBase64String(Encoding.Unicode.GetBytes(command)) }) options.ArgumentList.Add(arg);
        using Process query = Process.Start(options)!;
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            Task<string> output = query.StandardOutput.ReadToEndAsync(timeout.Token);
            Task<string> errors = query.StandardError.ReadToEndAsync(timeout.Token);
            await query.WaitForExitAsync(timeout.Token);
            string text = await output;
            _ = await errors;
            Require(query.ExitCode == 0 && text.Length <= 16384, "Bounded owned-child inventory");
            using JsonDocument json = JsonDocument.Parse(text);
            JsonElement[] entries = json.RootElement.ValueKind == JsonValueKind.Array ? json.RootElement.EnumerateArray().ToArray() : [json.RootElement];
            string executable = Path.GetFullPath(Path.Combine(runtime, "bin", "syncwatch-helper.exe"));
            JsonElement owned = entries.Single(entry => string.Equals(entry.GetProperty("ExecutablePath").GetString(), executable, StringComparison.OrdinalIgnoreCase)
                && entry.GetProperty("CommandLine").GetString()!.Contains("--admission-server", StringComparison.Ordinal));
            Process process = Process.GetProcessById(owned.GetProperty("ProcessId").GetInt32());
            try
            {
                Require(!process.HasExited && process.StartTime.ToUniversalTime() >= started
                    && string.Equals(process.MainModule!.FileName, executable, StringComparison.OrdinalIgnoreCase), "Exact current test child ownership");
                return process;
            }
            catch { process.Dispose(); throw; }
        }
        finally
        {
            if (!query.HasExited) { query.Kill(entireProcessTree: true); await query.WaitForExitAsync(); }
        }
    }

    private static int Port()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0); listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }
    private static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
}
