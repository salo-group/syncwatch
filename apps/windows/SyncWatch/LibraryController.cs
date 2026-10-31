using System.IO;
using System.Text;
using System.Windows.Threading;
using SyncWatch.Design;
using SyncWatch.Library;

namespace SyncWatch;

// Local ownership only. A catalog identifier is not proof of identical peer content.
internal sealed record LibraryMediaSelection(LocalMediaFile Media, LibraryFolderAccess Access, LocalTrackPreferences? TrackPreferences = null)
{
    public LocalTrackPreferences Tracks => TrackPreferences ?? new();
}

/// <summary>Application state; filesystem, access, persistence and design retain independent owners.</summary>
internal sealed class LibraryController(ILibraryAccessStore store, Dispatcher dispatcher, string? selectedId = null, IMediaMetadataReader? metadata = null) : IDisposable
{
    private readonly FileSystemLibrary scanner = new(SyncWatchSelectionMenu.ExternalFileFilters.SelectMany(filter =>
        filter.Extensions.Select(extension => (Extension: extension, Kind: filter.Kind == "audio" ? LibraryCompanionKind.Audio : LibraryCompanionKind.Subtitles)))
        .ToDictionary(item => item.Extension, item => item.Kind));
    private readonly LibraryDurationCache durations = new();
    private readonly SemaphoreSlim persistence = new(1, 1);
    private CancellationTokenSource? pending;
    private LibraryChangeMonitor? monitor;
    private LibraryFolderAccess? monitorAccess;
    private bool? monitorRecursive;
    private bool? configuredSubfolders;
    private bool? configuredRefresh;
    private CancellationTokenSource? monitorCancellation;
    private Task monitorTask = Task.CompletedTask;
    private readonly List<Task> retiredMonitors = [];
    private CancellationTokenSource? metadataCancellation;
    private long metadataGeneration;
    public Task MetadataSettled { get; private set; } = Task.CompletedTask;
    private LibraryFolderAccess? grant;
    private IReadOnlyDictionary<string, LocalMediaFile> catalog = new Dictionary<string, LocalMediaFile>();
    private LibraryPresentation? stable;
    private long generation;
    private bool closed;
    public LibraryPresentation Presentation { get; private set; } = new() { EnabledActions = new HashSet<string> { "chooseFolder", "openSettings" } };
    public LibraryTrackSelection Tracks { get; private set; } = new();
    public string? ErrorKey { get; private set; }
    public Guid RoomRevision { get; private set; } = Guid.NewGuid();
    public IReadOnlyList<LocalMediaFile> RoomFiles => catalog.Values.OrderBy(p => p.Id, StringComparer.Ordinal).ToArray();
    public event Action? Changed;
    public Task Settled { get; private set; } = Task.CompletedTask;

    public void Restore() => Start(async token =>
    {
        LibraryAccessRead value = await Storage(() => store.Read(), token);
        return value.Status switch
        {
            LibraryAccessStatus.Loaded => await Task.Run(() => LibraryFolderAccess.Restore(value.Record!), token),
            LibraryAccessStatus.Missing or LibraryAccessStatus.Invalid => null,
            _ => throw new InvalidOperationException("unsupported-library-version"),
        };
    });
    public void Choose(string path)
    {
        bool recursive = ScanRecursively(grant), refresh = RefreshAutomatically(grant);
        Start(token => Task.Run<LibraryFolderAccess?>(() => LibraryFolderAccess.Selected(path, recursive, refresh), token));
    }
    public void Rescan() { if (grant is not null) { var owner = grant; Start(_ => Task.FromResult<LibraryFolderAccess?>(owner), persistAccess: false); } }
    public string FolderPath => grant?.RootPath ?? "";
    public bool CanConfigure => !closed && Presentation.State != LibraryVisualState.Scanning;
    public SettingsEditor SettingsEditor(SettingsEditor basis)
    {
        var values = new Dictionary<string, string>(basis.Values)
        { ["scanSubfolders"] = ScanRecursively(grant) ? "true" : "false", ["automaticRefresh"] = RefreshAutomatically(grant) ? "true" : "false" };
        return new(values, basis.EditableFields.Concat(CanConfigure && basis.CanSave ? new[] { "scanSubfolders", "automaticRefresh" } : []), basis.CanSave);
    }
    public bool ConfigureOptions(bool? scanSubfolders, bool? automaticRefresh)
    {
        dispatcher.VerifyAccess();
        if (configuredSubfolders == scanSubfolders && configuredRefresh == automaticRefresh) return !closed;
        if (!CanConfigure) return false;
        configuredSubfolders = scanSubfolders; configuredRefresh = automaticRefresh; Rescan(); return true;
    }
    private bool ScanRecursively(LibraryFolderAccess? access) => configuredSubfolders ?? access?.Record.ScanSubfolders ?? (SyncWatchSettings.Defaults["scanSubfolders"] == "true");
    private bool RefreshAutomatically(LibraryFolderAccess? access) => configuredRefresh ?? access?.Record.AutoRefresh ?? (SyncWatchSettings.Defaults["automaticRefresh"] == "true");
    private bool SameRoomCatalog(IReadOnlyList<LocalMediaFile> files) => files.Count == catalog.Count && files.All(file =>
        catalog.TryGetValue(file.Id, out var old) && file.Title == old.Title && file.RelativePath == old.RelativePath &&
        file.SizeBytes == old.SizeBytes && file.ModificationDate == old.ModificationDate);
    public void Select(string id)
    {
        dispatcher.VerifyAccess(); if (closed) return;
        Presentation.Dispatch("selectMovie", id, (_, _) => { Presentation = Presentation with { SelectedId = id }; StartMetadata(); Changed?.Invoke(); });
    }
    public LibraryMediaSelection? SelectedMedia()
    {
        dispatcher.VerifyAccess();
        return !closed && Presentation.State == LibraryVisualState.Selected && grant is not null &&
            Presentation.SelectedId is string id && catalog.TryGetValue(id, out var media) ? new(media, grant, Tracks.Preferences) : null;
    }
    internal LibraryMediaSelection? PlaylistMedia(string id)
    {
        dispatcher.VerifyAccess();
        return !closed && Presentation.State != LibraryVisualState.Scanning && grant is not null && catalog.TryGetValue(id, out var media)
            ? new(media, grant, new()) : null;
    }
    public void OpenTracks(string kind, string language)
    {
        dispatcher.VerifyAccess();
        if (closed || !Presentation.Allows(kind == "audio" ? "openLibraryAudio" : "openLibrarySubtitles")) return;
        Tracks = Tracks.Open(kind, language); Changed?.Invoke();
    }
    public void SelectTrack(SelectionMenuRequest request)
    {
        dispatcher.VerifyAccess(); if (closed) return;
        Tracks = Tracks.Select(request);
        Presentation = Presentation with
        {
            MetadataAudioName = Tracks.AudioName,
            MetadataSubtitleName = Tracks.SubtitleName,
            MetadataSubtitlesDisabled = Tracks.SubtitlesDisabled
        };
        stable = Presentation; Changed?.Invoke();
    }
    public void DismissTracks() { if (Tracks.Menu is null) return; Tracks = Tracks.Dismiss(); Changed?.Invoke(); }
    public void ClearError() => ErrorKey = null;
    public void Dispose()
    {
        dispatcher.VerifyAccess(); if (closed) return; closed = true; Tracks = Tracks.Reset(); generation++; pending?.Cancel(); metadataGeneration++; metadataCancellation?.Cancel(); grant = null;
        monitorCancellation?.Cancel(); monitor?.Dispose(); monitor = null; monitorAccess = null;
        catalog = new Dictionary<string, LocalMediaFile>(); durations.Clear();
        Presentation = Presentation with { EnabledActions = new HashSet<string>() };
    }

    public async Task CloseAsync()
    {
        Dispose();
        await Settled; await MetadataSettled;
        await Task.WhenAll(retiredMonitors.Append(monitorTask));
        if (metadata is not null) await metadata.CloseAsync();
        metadataCancellation?.Dispose(); pending?.Dispose();
    }
    private void CancelMetadata()
    {
        metadataGeneration++; metadataCancellation?.Cancel(); Tracks = Tracks.Reset();
        Presentation = Presentation with
        {
            MetadataDurationMs = null,
            MetadataWidth = 0,
            MetadataHeight = 0,
            MetadataAudioTracks = null,
            MetadataSubtitleTracks = null,
            MetadataAudioName = null,
            MetadataSubtitleName = null,
            MetadataSubtitlesDisabled = false,
            MetadataLoading = false,
            MetadataUnavailable = false
        };
    }
    private void StartMetadata()
    {
        var previous = MetadataSettled;
        var previousCancellation = metadataCancellation;
        CancelMetadata(); stable = Presentation;
        if (metadata is null || SelectedMedia() is not { } selection) return;
        long current = metadataGeneration;
        metadataCancellation = new();
        Presentation = Presentation with { MetadataLoading = true }; stable = Presentation;
        var files = new[] { selection.Media }.Concat(Presentation.Entries.Select(row => catalog[row.Id]).Where(file => file.Id != selection.Media.Id)).ToArray();
        MetadataSettled = ReadMetadata(previous, previousCancellation, selection, files, current, metadataCancellation.Token);
    }
    private void PublishDuration(long? value, LocalMediaFile file)
    {
        durations.Record(value, file);
        Presentation = Presentation with { Entries = Presentation.Entries.Select(row => row.Id == file.Id ? row with { DurationMs = value } : row).ToArray() };
    }
    private async Task ReadMetadata(Task previous, CancellationTokenSource? previousCancellation,
        LibraryMediaSelection selection, IReadOnlyList<LocalMediaFile> files, long current, CancellationToken token)
    {
        try
        {
            await previous; previousCancellation?.Dispose(); token.ThrowIfCancellationRequested();
            foreach (var file in files)
            {
                token.ThrowIfCancellationRequested(); if (closed || current != metadataGeneration) return;
                bool selected = file.Id == selection.Media.Id;
                if (!selected && durations.Duration(file) is not null) continue;
                try
                {
                    var value = await metadata!.ReadAsync(file, token);
                    token.ThrowIfCancellationRequested(); if (closed || current != metadataGeneration) return;
                    PublishDuration(value.DurationMs, file);
                    if (selected)
                    {
                        Tracks = Tracks.Reset(value, file.Companions);
                        Presentation = Presentation with
                        {
                            MetadataDurationMs = value.DurationMs,
                            MetadataWidth = value.Width,
                            MetadataHeight = value.Height,
                            MetadataAudioTracks = value.AudioTracks + file.Companions.Count(f => f.Kind == LibraryCompanionKind.Audio),
                            MetadataSubtitleTracks = value.SubtitleTracks + file.Companions.Count(f => f.Kind == LibraryCompanionKind.Subtitles),
                            MetadataLoading = false,
                            MetadataAudioName = Tracks.AudioName,
                            MetadataSubtitleName = Tracks.SubtitleName,
                            MetadataSubtitlesDisabled = Tracks.SubtitlesDisabled,
                            EnabledActions = new HashSet<string>(Presentation.EnabledActions.Concat(["openLibraryAudio", "openLibrarySubtitles"]))
                        };
                    }
                    stable = Presentation; Changed?.Invoke();
                }
                catch (OperationCanceledException) { return; }
                catch (Exception)
                {
                    if (closed || current != metadataGeneration) return;
                    PublishDuration(null, file);
                    if (selected) Presentation = Presentation with { MetadataLoading = false, MetadataUnavailable = true };
                    stable = Presentation; Changed?.Invoke();
                }
            }
        }
        catch (OperationCanceledException) { }
        finally { GC.KeepAlive(selection.Access); }
    }

    private async Task<T> Storage<T>(Func<T> operation, CancellationToken token)
    {
        await persistence.WaitAsync(token);
        try { return await Task.Run(operation, token); }
        finally { persistence.Release(); }
    }
    private void ReplaceMonitor(LibraryChangeMonitor? source, LibraryFolderAccess access, bool recursive)
    {
        monitorCancellation?.Cancel(); monitor?.Dispose();
        retiredMonitors.RemoveAll(task => task.IsCompleted);
        if (!monitorTask.IsCompleted) retiredMonitors.Add(monitorTask);
        monitor = source; monitorAccess = source is null ? null : access;
        monitorRecursive = source is null ? null : recursive;
        monitorCancellation = source is null ? null : new();
        monitorTask = source is null ? Task.CompletedTask : MonitorChanges(source, access, monitorCancellation!);
    }
    private async Task MonitorChanges(LibraryChangeMonitor source, LibraryFolderAccess access, CancellationTokenSource cancellation)
    {
        try
        {
            await foreach (var change in source.Changes(cancellation.Token))
            {
                await Task.Delay(300, cancellation.Token);
                await Settled;
                cancellation.Token.ThrowIfCancellationRequested();
                if (closed || !ReferenceEquals(monitor, source)) return;
                Start(_ => Task.FromResult<LibraryFolderAccess?>(access), automatic: true, persistAccess: false);
                await Settled;
                if (change == LibraryMonitorEvent.Unavailable || !source.IsActive)
                {
                    // Reconciliation owns failures even if the UI already consumed its error.
                    return;
                }
            }
        }
        catch (OperationCanceledException) { }
        finally
        {
            source.Dispose();
            if (ReferenceEquals(monitor, source)) monitorCancellation = null;
            cancellation.Dispose(); GC.KeepAlive(access);
        }
    }
    private bool SameCatalog(IReadOnlyList<LocalMediaFile> files) => files.Count == catalog.Count && files.All(file =>
        catalog.TryGetValue(file.Id, out var old) && file.Title == old.Title && file.RelativePath == old.RelativePath &&
        file.File == old.File && file.SizeBytes == old.SizeBytes && file.ModificationDate == old.ModificationDate &&
        file.FolderMovie == old.FolderMovie && file.Companions.SequenceEqual(old.Companions));

    private void Start(Func<CancellationToken, Task<LibraryFolderAccess?>> acquire, bool automatic = false, bool persistAccess = true)
    {
        dispatcher.VerifyAccess(); if (closed) return;
        pending?.Cancel(); if (!automatic) CancelMetadata(); pending = new(); long current = ++generation;
        Settled = Scan(acquire, pending.Token, current, automatic, persistAccess);
    }
    private async Task Scan(Func<CancellationToken, Task<LibraryFolderAccess?>> acquire, CancellationToken token, long current, bool automatic, bool persistAccess)
    {
        var previous = stable ?? Presentation; stable = previous;
        if (!automatic) { ErrorKey = null; Presentation = previous with { State = LibraryVisualState.Scanning }; Changed?.Invoke(); }
        LibraryChangeMonitor? candidate = null;
        try
        {
            var next = await acquire(token);
            token.ThrowIfCancellationRequested();
            if (next is null) { if (generation == current) { Presentation = previous; StartMetadata(); Changed?.Invoke(); } return; }
            bool recursive = ScanRecursively(next), refresh = RefreshAutomatically(next);
            bool reuse = refresh && monitor?.IsActive == true && monitorAccess?.RootId == next.RootId &&
                monitorAccess?.RootPath == next.RootPath && monitorRecursive == recursive;
            bool watchFailed = false;
            if (refresh && !reuse)
            {
                try { candidate = await Task.Run(() => new LibraryChangeMonitor(next.RootPath, recursive), token); }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { watchFailed = true; }
            }
            token.ThrowIfCancellationRequested();
            var files = await scanner.ScanAsync(next.RootPath, next.RootId, recursive, token);
            token.ThrowIfCancellationRequested(); if (closed || generation != current) return;
            if (automatic && SameCatalog(files))
            {
                if (!reuse) { ReplaceMonitor(candidate, next, recursive); candidate = null; }
                if (watchFailed) { ErrorKey = "libraryWatchFailed"; Changed?.Invoke(); }
                return;
            }
            if (automatic) CancelMetadata();
            // Local companion and folder-presentation changes do not publish room activity.
            bool publishRoom = !automatic || !SameRoomCatalog(files);
            var utf8 = Comparer<string>.Create((a, b) => Encoding.UTF8.GetBytes(a).AsSpan().SequenceCompareTo(Encoding.UTF8.GetBytes(b)));
            durations.Reconcile(files);
            var rows = files.OrderBy(x => x.Title.ToLowerInvariant(), utf8).ThenBy(x => x.RelativePath, utf8)
                .Select(x => new LibraryEntry(x.Id, x.Title, x.File, durations.Duration(x), x.FolderMovie)).ToArray();
            string? selected = new[] { automatic ? Presentation.SelectedId : previous.SelectedId, selectedId }.FirstOrDefault(id => id is not null && rows.Any(x => x.Id == id)) ?? rows.FirstOrDefault()?.Id;
            var nextCatalog = files.ToDictionary(x => x.Id);
            grant = next; catalog = nextCatalog; if (publishRoom) RoomRevision = Guid.NewGuid(); Presentation = new()
            {
                State = rows.Length == 0 ? LibraryVisualState.Empty : LibraryVisualState.Selected,
                Entries = rows,
                SelectedId = selected,
                EnabledActions = new HashSet<string> { "chooseFolder", "rescan", "search", "selectMovie", "openSettings" }
            };
            StartMetadata(); Changed?.Invoke();
            if (!reuse) { ReplaceMonitor(candidate, next, recursive); candidate = null; }
            if (watchFailed) { ErrorKey = "libraryWatchFailed"; Changed?.Invoke(); }
            if (!persistAccess) return;
            try { await Storage(() => { store.Write(next.Record); return true; }, token); }
            catch (OperationCanceledException) { }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException)
            { if (generation == current) { ErrorKey = "librarySaveFailed"; Changed?.Invoke(); } }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException)
        {
            if (!closed && generation == current)
            {
                if (!automatic) { Presentation = previous; StartMetadata(); }
                ErrorKey = "libraryUnavailable"; Changed?.Invoke();
            }
        }
        finally { candidate?.Dispose(); }
    }
}
