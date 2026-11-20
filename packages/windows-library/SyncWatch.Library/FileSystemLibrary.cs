using System.Security.Cryptography;
using System.Text;

namespace SyncWatch.Library;

/// <summary>Local filesystem facts; paths and timestamps never form a peer descriptor.</summary>
public sealed record LocalMediaFile(string Id, string Title, string RelativePath, string File,
    long SizeBytes, DateTimeOffset ModificationDate)
{
    public IReadOnlyList<LocalCompanionFile> Companions { get; init; } = Array.Empty<LocalCompanionFile>();
    public bool FolderMovie { get; init; }
}

/// <summary>The caller retains the selected folder grant. Publish only completed snapshots.</summary>
public sealed class FileSystemLibrary
{
    private readonly IReadOnlyDictionary<string, LibraryCompanionKind> companionExtensions;
    public FileSystemLibrary(IReadOnlyDictionary<string, LibraryCompanionKind>? companionExtensions = null)
    {
        this.companionExtensions = companionExtensions is null ? new Dictionary<string, LibraryCompanionKind>()
            : new Dictionary<string, LibraryCompanionKind>(companionExtensions, StringComparer.OrdinalIgnoreCase);
    }
    private static readonly HashSet<string> Extensions = new(StringComparer.OrdinalIgnoreCase)
        { ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts" };
    private const int MaximumDirectoryDepth = 128;

    public Task<IReadOnlyList<LocalMediaFile>> ScanAsync(string root, string rootId, bool recursive, CancellationToken cancellation = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        if (!Guid.TryParseExact(rootId, "D", out var id) || id.ToString("D") != rootId) throw new ArgumentException("library-root-id", nameof(rootId));
        if (!Path.IsPathFullyQualified(root)) throw new ArgumentException("absolute-library-root", nameof(root));
        return Task.Run<IReadOnlyList<LocalMediaFile>>(() => Scan(Path.GetFullPath(root), rootId, recursive, cancellation), cancellation);
    }

    private IReadOnlyList<LocalMediaFile> Scan(string root, string rootId, bool recursive, CancellationToken cancellation)
    {
        var files = new List<LocalMediaFile>();
        var companions = new List<LocalCompanionFile>();
        var caseSensitive = new Dictionary<string, bool>(StringComparer.Ordinal);
        void Visit(LibraryDirectory directory, string relativeDirectory, int depth)
        {
            if (depth >= MaximumDirectoryDepth) throw new IOException("library-depth");
            cancellation.ThrowIfCancellationRequested();
            List<LibraryDirectory.Entry> children = directory.ReadEntries(cancellation);
            var names = children.Select(entry => entry.Name).ToHashSet(StringComparer.Ordinal);
            foreach (var entry in children)
            {
                cancellation.ThrowIfCancellationRequested();
                if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                string filename = entry.Name;
                string relative = relativeDirectory + filename;
                if ((entry.Attributes & FileAttributes.Directory) != 0)
                {
                    if (recursive)
                    {
                        using var child = directory.OpenChild(filename);
                        Visit(child, relative + "/", depth + 1);
                    }
                    continue;
                }
                int dot = filename.LastIndexOf('.');
                if (dot < 0) continue;
                string extensionName = filename[(dot + 1)..];
                bool video = Extensions.Contains(filename[dot..]);
                bool extra = companionExtensions.TryGetValue(extensionName, out var kind);
                if (!video && !extra) continue;
                if (entry.Size < 0) throw new IOException("library-file-size");
                string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                var modified = DateTimeOffset.FromFileTime(entry.LastWriteTime).ToUniversalTime();
                string id = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(rootId + "\0" + relative)));
                if (!video)
                {
                    companions.Add(new(id, kind, relative, path, entry.Size, modified));
                    continue;
                }
                if (!caseSensitive.ContainsKey(relativeDirectory))
                {
                    // Read-only probe of a known ASCII extension in the actual directory.
                    string alternate = filename[..(dot + 1)] + new string(extensionName.Select(c =>
                        char.IsUpper(c) ? char.ToLowerInvariant(c) : char.ToUpperInvariant(c)).ToArray());
                    caseSensitive[relativeDirectory] = names.Contains(alternate) || !directory.Exists(alternate);
                }
                files.Add(new(id, filename, relative, path, entry.Size, modified));
            }
        }
        using (var directory = LibraryDirectory.OpenRoot(root)) Visit(directory, "", 0);
        cancellation.ThrowIfCancellationRequested();
        string[] paths = files.Select(f => f.RelativePath).ToArray();
        string[] audio = companions.Where(f => f.Kind == LibraryCompanionKind.Audio).Select(f => f.RelativePath).ToArray();
        string[] subtitles = companions.Where(f => f.Kind == LibraryCompanionKind.Subtitles).Select(f => f.RelativePath).ToArray();
        var sensitive = new LibraryFileIndex(paths, audio, subtitles, true);
        var insensitive = new LibraryFileIndex(paths, audio, subtitles, false);
        var byPath = companions.ToDictionary(f => f.RelativePath, StringComparer.Ordinal);
        for (int position = 0; position < files.Count; position++)
        {
            cancellation.ThrowIfCancellationRequested();
            var file = files[position]; int slash = file.RelativePath.LastIndexOf('/');
            string directory = slash < 0 ? "" : file.RelativePath[..(slash + 1)];
            var index = caseSensitive[directory] ? sensitive : insensitive;
            var extras = index.AudioPaths(file.RelativePath).Concat(index.SubtitlePaths(file.RelativePath)).Select(p => byPath[p]).ToList().AsReadOnly();
            files[position] = file with { Companions = extras, FolderMovie = index.IsFolderMovie(file.RelativePath) };
        }
        files.Sort((a, b) => Encoding.UTF8.GetBytes(a.RelativePath).AsSpan().SequenceCompareTo(Encoding.UTF8.GetBytes(b.RelativePath)));
        return files.AsReadOnly();
    }
}
