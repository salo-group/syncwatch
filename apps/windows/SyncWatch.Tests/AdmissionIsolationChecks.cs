using System.IO;
using System.Text.Json;
using SyncWatch;
using SyncWatch.Room;

internal static class AdmissionIsolationChecks
{
    internal static async Task RunAsync()
    {
        string? path = Environment.GetEnvironmentVariable("SYNCWATCH_ADMISSION_ISOLATION_FIXTURE");
        if (path is null) return;
        using var fixture = JsonDocument.Parse(File.ReadAllBytes(path)); var value = fixture.RootElement;
        var identityData = value.GetProperty("identity");
        var identity = new RoomIdentity(identityData.GetProperty("clientRunId").GetString()!, identityData.GetProperty("syncplayUsername").GetString()!);
        var admission = RoomAdmission.Decode(JsonSerializer.SerializeToUtf8Bytes(value.GetProperty("admission")), identity, true);
        var address = new RoomAddress(value.GetProperty("service").GetString()!);
        await using var client = new RoomClient(address); await using var events = new RoomClient(address);
        var owner = new RoomMembershipSession(client, admission, identity);
        using var stopping = new CancellationTokenSource();
        Task? connecting = null; Task<RoomWatchContext>? reading = null;
        try
        {
            connecting = owner.ConnectEventsAsync(events, false, stopping.Token);
            var deadline = DateTime.UtcNow.AddSeconds(3);
            while (!File.Exists(value.GetProperty("held").GetString()!))
            {
                if (DateTime.UtcNow >= deadline) throw new TimeoutException("Event fixture did not hold the request");
                await Task.Delay(10);
            }
            reading = owner.AdmissionContextAsync(admission.Bootstrap, stopping.Token);
            if (await Task.WhenAny(reading, Task.Delay(2000)) != reading) throw new Exception("Admission waited for the held event bootstrap");
            var context = await reading;
            if (context.Definition.WatchGeneration != 2 || context.Definition.Sequence != 8 || context.Definition.Selection?.Media.MediaId != "movie")
                throw new Exception("Admission did not validate the fresh edition");
            foreach (var valueAnchor in value.GetProperty("invalidAnchors").EnumerateArray())
            {
                var anchor = RoomBootstrap.Decode(JsonSerializer.SerializeToUtf8Bytes(valueAnchor));
                bool rejected = false;
                try { _ = await owner.AdmissionContextAsync(anchor, stopping.Token); }
                catch (RoomProtocolException error) when (error.Message == "malformed") { rejected = true; }
                if (!rejected) throw new Exception("Admission accepted a foreign binding");
            }
            var advanced = RoomAdmission.Decode(JsonSerializer.SerializeToUtf8Bytes(value.GetProperty("advancedAdmission")), identity, true);
            var newer = new RoomMembershipSession(client, advanced, identity);
            bool stale = false;
            try { _ = await newer.AdmissionContextAsync(admission.Bootstrap, stopping.Token); }
            catch (RoomProtocolException error) when (error.Message == "malformed") { stale = true; }
            if (!stale) throw new Exception("Admission accepted a selection below the membership sequence floor");
            if (File.Exists(value.GetProperty("unexpectedBootstrap").GetString()!)) throw new Exception("Admission replaced the catalog cursor");
            Console.WriteLine("SYNCWATCH_ADMISSION_ISOLATION_OK held event bootstrap, fresh edition, retained cursor");
        }
        finally
        {
            await stopping.CancelAsync(); await events.DisposeAsync();
            if (connecting is not null) { try { await connecting; } catch (Exception) when (stopping.IsCancellationRequested) { } }
            if (reading is not null) { try { await reading; } catch (Exception) when (stopping.IsCancellationRequested) { } }
        }
    }
}
