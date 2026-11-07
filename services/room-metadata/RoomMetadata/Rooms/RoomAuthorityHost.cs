namespace SyncWatch.RoomMetadata.Rooms;

/// <summary>The HTTP authority and its only enforcing Syncplay listener share one lifetime.</summary>
internal sealed class RoomAuthorityHost(RoomAuthorityOptions options, AuthenticatedRooms rooms,
    IHostApplicationLifetime lifetime) : IHostedService, IAsyncDisposable
{
    private SyncServerProcess? owner;
    private Task? monitor, sweep;
    private readonly CancellationTokenSource stopping = new();
    private int closing, faulted;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        var authority = new SyncAdmissionAuthority(rooms, options.PublicHost, options.Port);
        owner = await SyncServerProcess.StartAsync(new(options.Runtime, options.TlsDirectory, options.BindAddress, options.Port),
            authority, cancellationToken);
        monitor = Monitor(owner);
        sweep = Sweep();
    }

    private async Task Monitor(SyncServerProcess process)
    {
        try { await process.Completion; }
        catch (Exception) { /* No raw child diagnostics or credential-bearing frames. */ }
        finally { if (Volatile.Read(ref closing) == 0) Fail(); }
    }

    private async Task Sweep()
    {
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
            while (await timer.WaitForNextTickAsync(stopping.Token)) rooms.Sweep();
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { }
        catch (Exception) { Fail(); }
    }

    private void Fail()
    {
        if (Interlocked.Exchange(ref faulted, 1) != 0) return;
        Console.Error.WriteLine("Synchronization authority stopped unexpectedly.");
        Environment.ExitCode = 1;
        lifetime.StopApplication();
    }

    public Task StopAsync(CancellationToken cancellationToken) => DisposeAsync().AsTask();

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref closing, 1) != 0) return;
        stopping.Cancel();
        try { if (owner is not null) await owner.DisposeAsync(); }
        catch (IOException) { Fail(); }
        finally
        {
            if (sweep is not null) await sweep;
            if (monitor is not null) await monitor;
            stopping.Dispose();
        }
    }
}
