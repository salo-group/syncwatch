using System.Diagnostics;
using System.Net;
using System.Text.Json;
using System.Threading.Channels;

namespace SyncWatch.RoomMetadata.Rooms;

public sealed record SyncServerLaunch(string Runtime, string TlsDirectory, string BindAddress, int Port);

/// <summary>One owned helper process and its bounded private authority channel.</summary>
public sealed class SyncServerProcess : IAsyncDisposable
{
    private readonly Process process;
    private readonly SyncAdmissionAuthority authority;
    private readonly int expectedPort;
    private readonly object gate = new();
    private readonly CancellationTokenSource stop = new();
    private readonly Channel<byte[]> outgoing = Channel.CreateBounded<byte[]>(512);
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly HashSet<string> requests = new(StringComparer.Ordinal);
    private readonly Queue<string> requestOrder = new();
    private readonly Task reader, writer, renewer, diagnostic, exited;
    private readonly Task completion;
    private bool receivedStop;
    private int closing;
    private bool failed;
    public Task Completion => completion;

    private SyncServerProcess(Process process, SyncAdmissionAuthority authority, int expectedPort)
    {
        this.process = process; this.authority = authority; this.expectedPort = expectedPort;
        reader = Read(); writer = Write(); renewer = Renew(); diagnostic = Drain(process.StandardError.BaseStream);
        exited = process.WaitForExitAsync(); completion = Monitor();
    }

    public static async Task<SyncServerProcess> StartAsync(SyncServerLaunch launch, SyncAdmissionAuthority authority,
        CancellationToken cancellation = default)
    {
        Wire.Need(Path.IsPathFullyQualified(launch.Runtime) && Path.IsPathFullyQualified(launch.TlsDirectory)
            && IPAddress.TryParse(launch.BindAddress, out _) && launch.Port is >= 1 and <= 65535);
        string executable = Path.Combine(launch.Runtime, "bin", OperatingSystem.IsWindows() ? "syncwatch-helper.exe" : "syncwatch-helper");
        Wire.Need(File.Exists(executable) && Directory.Exists(launch.TlsDirectory), "syncServerUnavailable", 503);
        ProcessStartInfo options = new(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = launch.Runtime,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        options.ArgumentList.Add(launch.Runtime); options.ArgumentList.Add("--admission-server");
        HashSet<string> allowedEnvironment = new(["SystemRoot", "WINDIR", "TEMP", "TMP"], StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string?> inherited = options.Environment.Where(item => allowedEnvironment.Contains(item.Key))
            .ToDictionary(item => item.Key, item => item.Value);
        options.Environment.Clear();
        foreach ((string key, string? value) in inherited) options.Environment[key] = value;
        var owner = new SyncServerProcess(Process.Start(options) ?? throw new IOException("Synchronization server did not start"), authority, launch.Port);
        try
        {
            lock (owner.gate) owner.Send(new { v = 1, type = "server.start", launch.BindAddress, launch.Port, launch.TlsDirectory, salt = Wire.Secret() });
            await owner.ready.Task.WaitAsync(TimeSpan.FromSeconds(10), cancellation);
            return owner;
        }
        catch { await owner.DisposeAsync(); throw; }
    }

    private void Send(object value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json);
        if (bytes.Length > 16383 || !outgoing.Writer.TryWrite([.. bytes, (byte)'\n'])) throw new IOException("Authority channel unavailable");
    }

    private async Task Write()
    {
        try
        {
            await foreach (byte[] bytes in outgoing.Reader.ReadAllAsync(stop.Token))
            {
                await process.StandardInput.BaseStream.WriteAsync(bytes, stop.Token);
                await process.StandardInput.BaseStream.FlushAsync(stop.Token);
            }
        }
        finally { process.StandardInput.Close(); }
    }

    private async Task Read()
    {
        byte[] buffer = new byte[4096];
        using MemoryStream frame = new();
        while (true)
        {
            int count = await process.StandardOutput.BaseStream.ReadAsync(buffer, stop.Token);
            if (count == 0)
            {
                if (frame.Length != 0 || !receivedStop) throw new IOException("Authority channel closed");
                return;
            }
            for (int index = 0; index < count; index++)
            {
                byte item = buffer[index];
                if (item == '\n')
                {
                    JsonElement value = Wire.Parse(frame.ToArray()); frame.SetLength(0);
                    lock (gate) Receive(value);
                }
                else
                {
                    if (frame.Length >= 16383) throw new IOException("Authority frame too large");
                    frame.WriteByte(item);
                }
            }
        }
    }

    private void Receive(JsonElement value)
    {
        Wire.Need(Wire.Integer(value, "v", 1) == 1);
        string kind = Wire.String(value, "type");
        if (!ready.Task.IsCompleted)
        {
            Wire.Fields(value, "v", "type", "port");
            Wire.Need(kind == "server.ready" && Wire.Integer(value, "port", 65535) == expectedPort);
            ready.SetResult(); return;
        }
        if (kind == "server.stopped")
        {
            Wire.Fields(value, "v", "type"); Wire.Need(Volatile.Read(ref closing) != 0 && !receivedStop);
            receivedStop = true; return;
        }
        Wire.Need(!receivedStop);
        if (Volatile.Read(ref closing) != 0) return;
        if (kind == "admission.consume")
        {
            Wire.Fields(value, "v", "type", "id", "ticket", "clientRunId", "username", "room");
            string id = Wire.String(value, "id");
            Wire.Need(id.Length == 32 && id.All(char.IsAsciiHexDigitLower) && requests.Add(id));
            requestOrder.Enqueue(id); if (requestOrder.Count > 512) requests.Remove(requestOrder.Dequeue());
            // This gate also owns renewals: a lease result is enqueued before its first renewal.
            try
            {
                SyncAuthorityLease lease = authority.Consume(Wire.String(value, "ticket"), Wire.String(value, "clientRunId"),
                    Wire.String(value, "username"), Wire.String(value, "room"));
                Send(new { v = 1, type = "admission.result", replyTo = id, status = "accepted", lease });
            }
            catch (RoomException) { Send(new { v = 1, type = "admission.result", replyTo = id, status = "denied" }); }
        }
        else if (kind == "admission.closed")
        {
            Wire.Fields(value, "v", "type", "leaseId"); string id = Wire.String(value, "leaseId"); Wire.Need(Wire.Id(id));
            authority.Release(id);
        }
        else throw new IOException("Unknown authority operation");
    }

    private async Task Renew()
    {
        using PeriodicTimer timer = new(TimeSpan.FromSeconds(1));
        while (await timer.WaitForNextTickAsync(stop.Token))
        {
            lock (gate)
            {
                if (Volatile.Read(ref closing) != 0) return;
                if (!ready.Task.IsCompletedSuccessfully) continue;
                foreach (SyncAuthorityUpdate update in authority.Updates())
                {
                    if (update.Lease is SyncAuthorityLease lease) Send(new { v = 1, type = "lease.renew", lease });
                    else Send(new { v = 1, type = "lease.revoke", leaseId = update.Id });
                }
            }
        }
    }

    private static async Task Drain(Stream stream)
    {
        byte[] discard = new byte[1024];
        try { while (await stream.ReadAsync(discard) != 0) { } }
        catch (IOException) { }
    }

    private async Task Monitor()
    {
        await Task.WhenAny(reader, writer, renewer, exited);
        if (Volatile.Read(ref closing) == 0)
        {
            failed = true; ready.TrySetException(new IOException("Synchronization server unavailable")); Kill();
        }
        try { await exited.WaitAsync(TimeSpan.FromSeconds(5)); }
        catch (TimeoutException) { failed = true; Kill(); await exited; }
        stop.Cancel(); outgoing.Writer.TryComplete();
        foreach (Task task in new[] { reader, writer, renewer, diagnostic })
        {
            try { await task; }
            catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
            catch (Exception) { failed = true; }
        }
        authority.Clear();
        if (failed || process.ExitCode != 0 || !receivedStop) throw new IOException("Synchronization server stopped unexpectedly");
    }

    private void Kill()
    {
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref closing, 1) == 0)
        {
            lock (gate)
            {
                try { Send(new { v = 1, type = "server.shutdown" }); }
                catch (IOException) { failed = true; Kill(); }
                outgoing.Writer.TryComplete();
            }
        }
        try { await completion; }
        finally { process.Dispose(); stop.Dispose(); }
    }
}
