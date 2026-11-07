using System.IO;
using System.Text.Json;
using SyncWatch;
using SyncWatch.Library;

internal static class RoomPublicationLiveChecks
{
    internal static async Task RunAsync()
    {
        string? room = Environment.GetEnvironmentVariable("SYNCWATCH_PUBLICATION_ROOM"); if (room is null) return;
        string caseRoot = Environment.GetEnvironmentVariable("SYNCWATCH_PUBLICATION_CASE")!;
        string directory = Path.Combine(caseRoot, "movies"); if (Directory.Exists(directory)) throw new Exception("Owned publication directory already exists"); Directory.CreateDirectory(directory);
        foreach (string suffix in new[] { "A", "B" }) File.WriteAllText(Path.Combine(directory, $"Windows publication {suffix}.mkv"), "authored catalog fixture");
        string rootId = Guid.NewGuid().ToString("D"); var scanner = new FileSystemLibrary(); var controller = new RoomController(legacyValidation: true);
        async Task WaitAsync(Func<bool> condition, bool allowPublicationFailure = false)
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            while (!condition())
            {
                if (controller.Editor.State == "failed" || (!allowPublicationFailure && controller.Editor.Status == "roomCatalogPublishFailed")) throw new Exception("Native publication failed");
                await Task.Delay(20, deadline.Token);
            }
        }
        async Task WaitFileAsync(string name)
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            while (!File.Exists(Path.Combine(caseRoot, name))) await Task.Delay(20, deadline.Token);
        }
        void Signal(string name) => File.WriteAllText(Path.Combine(caseRoot, name), JsonSerializer.Serialize(new { passed = true }));
        try
        {
            var files = await scanner.ScanAsync(directory, rootId, false); controller.UpdateLibrary(Guid.NewGuid(), () => files);
            controller.Editor.SelectMode("joinRoom");
            foreach (var (field, value) in new[] { ("serviceAddress", Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_SERVICE")!), ("username", "Windows normal publisher"), ("roomID", room), ("invitation", Environment.GetEnvironmentVariable("SYNCWATCH_PUBLICATION_INVITATION")!) }) controller.Editor.Edit(field, value);
            controller.Connect();
            var expected = new[] { "macOS", "Windows", "Linux" }.SelectMany(name => new[] { $"{name} publication A", $"{name} publication B" }).ToHashSet();
            await WaitAsync(() => controller.CatalogView is { } view && view.Items.Select(p => p.Media.Title).ToHashSet().SetEquals(expected));
            var original = controller.CatalogView!;
            if (original.Items.Count != 6 || original.Items.Select(p => p.Media.MediaId).Distinct().Count() != 6) throw new Exception("Initial three-native catalog");
            string ownId = original.Items.Single(p => p.Media.Title == "Windows publication A").Media.MediaId;
            Signal("phase1.json"); await WaitAsync(() => File.Exists(Path.Combine(caseRoot, "phase2")));
            File.Delete(Path.Combine(directory, "Windows publication B.mkv"));
            files = await scanner.ScanAsync(directory, rootId, false); controller.UpdateLibrary(Guid.NewGuid(), () => files);
            var finalTitles = new[] { "macOS", "Windows", "Linux" }.Select(name => $"{name} publication A").ToHashSet();
            await WaitAsync(() => controller.CatalogView is { } view && view.Items.Count == 3 && view.Items.Select(p => p.Media.Title).ToHashSet().SetEquals(finalTitles));
            if (controller.CatalogView!.Items.Single(p => p.Media.Title == "Windows publication A").Media.MediaId != ownId || original.Items.Count != 6) throw new Exception("Stable identity/immutable view");
            Signal("phase2.json");
            if (Environment.GetEnvironmentVariable("SYNCWATCH_PUBLICATION_RECOVERY") == "1")
            {
                await WaitFileAsync("fail-network");
                files = await scanner.ScanAsync(directory, rootId, false); controller.UpdateLibrary(Guid.NewGuid(), () => files);
                await WaitAsync(() => controller.Editor.Status == "roomCatalogPublishFailed");
                if (!controller.Connected || controller.CatalogView?.Items.Count != 3) throw new Exception("Uncertain command destroyed membership");
                Signal("failure.json"); await WaitFileAsync("retry-network");
                string changed = "authored catalog fixture updated after unknown outcome";
                File.WriteAllText(Path.Combine(directory, "Windows publication A.mkv"), changed);
                files = await scanner.ScanAsync(directory, rootId, false); controller.UpdateLibrary(Guid.NewGuid(), () => files);
                await WaitAsync(() => controller.Editor.Status == "" && controller.CatalogView is { } view && view.Items.Count == 3 && view.Items.All(p => p.Media.FileSizeBytes == System.Text.Encoding.UTF8.GetByteCount(changed)), allowPublicationFailure: true);
                if (!controller.Connected || controller.CatalogView!.Items.Single(p => p.Media.Title == "Windows publication A").Media.MediaId != ownId) throw new Exception("Recovery changed membership or media identity");
                Signal("recovered.json");
            }
            await WaitAsync(() => File.Exists(Path.Combine(caseRoot, "finish")));
            await controller.CloseAsync(); await controller.CloseAsync();
            if (controller.CatalogView is not null || controller.Connected) throw new Exception("Publication close");
            Signal("closed.json"); Console.WriteLine("SYNCWATCH_ROOM_PUBLICATION_LIVE_OK all3 catalogs/atomic replacement/removal/close");
        }
        finally { await controller.CloseAsync(); Directory.Delete(directory, true); }
    }
}
