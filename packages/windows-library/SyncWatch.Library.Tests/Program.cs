using System.Text.Json;
using SyncWatch.Library;

MediaMetadataChecks.Run();
LibraryFileIndexChecks.Run(args.Single());
await LibraryCompanionScanChecks.RunAsync(args.Single());
await LibraryChangeMonitorChecks.RunAsync(args.Single());
await FileContentHasherTests.RunAsync(args.Single());

using var corpus = JsonDocument.Parse(File.ReadAllBytes(args.Single()));
var data = corpus.RootElement;
string id = data.GetProperty("rootId").GetString()!;
string root = Path.Combine(Path.GetTempPath(), "syncwatch-library-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
try
{
    foreach (var file in data.GetProperty("files").EnumerateArray())
    {
        string path = Path.Combine(root, file.GetProperty("path").GetString()!.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, file.GetProperty("content").GetString());
    }
    bool linksCreated = false;
    try
    {
        File.CreateSymbolicLink(Path.Combine(root, "link.mp4"), Path.Combine(root, "root.mkv"));
        Directory.CreateSymbolicLink(Path.Combine(root, "nested", "cycle"), root);
        linksCreated = true;
    }
    catch (UnauthorizedAccessException) { Console.WriteLine("SKIP: Windows symbolic-link creation privilege unavailable"); }
    var scanner = new FileSystemLibrary();
    if (linksCreated)
    {
        try { await scanner.ScanAsync(Path.Combine(root, "nested", "cycle"), id, true); throw new Exception("link-root-accepted"); }
        catch (IOException) { }
    }
    var expected = data.GetProperty("expected").EnumerateArray().ToArray();
    var result = await scanner.ScanAsync(root, id, true);
    Require(result.Select(x => x.RelativePath).SequenceEqual(expected.Select(x => x.GetProperty("relativePath").GetString())), "recursive-paths");
    Require(result.Select(x => x.Id).SequenceEqual(expected.Select(x => x.GetProperty("id").GetString())), "stable-id");
    Require(result.Select(x => x.SizeBytes).SequenceEqual(expected.Select(x => x.GetProperty("sizeBytes").GetInt64())), "size");
    Require(result.Select(x => x.Title).SequenceEqual(expected.Select(x => x.GetProperty("title").GetString())), "title");
    var flat = await scanner.ScanAsync(root, id, false);
    Require(flat.Select(x => x.RelativePath).SequenceEqual(expected.Select(x => x.GetProperty("relativePath").GetString()!).Where(x => !x.Contains('/'))), "nonrecursive");
    var other = await scanner.ScanAsync(root, data.GetProperty("otherRootId").GetString()!, true);
    Require(!result.Select(x => x.Id).Intersect(other.Select(x => x.Id)).Any(), "root-namespace");
    using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
    try { await scanner.ScanAsync(root, id, true, cancelled.Token); throw new Exception("cancelled-snapshot"); }
    catch (OperationCanceledException) { }
    try { await scanner.ScanAsync(Path.Combine(root, "absent"), id, true); throw new Exception("missing-root-accepted"); }
    catch (IOException error) when (error is FileNotFoundException or DirectoryNotFoundException) { }
    try { await scanner.ScanAsync(root, "invalid", true); throw new Exception("invalid-id-accepted"); }
    catch (ArgumentException) { }
    LibraryAccessTests.Run(args.Single(), root);
    Console.WriteLine($"SYNCWATCH_LIBRARY_OK entries={result.Count} symlinkChecks={linksCreated}");
}
finally { Directory.Delete(root, true); }

static void Require(bool value, string name) { if (!value) throw new InvalidOperationException(name); }
