using System.Windows.Threading;
using SyncWatch.Design;
using SyncWatch.Domain;

namespace SyncWatch;

// Executes local resource effects. Shared playback commands belong to the synchronization adapter.
internal sealed class WatchSessionCoordinator
{
    private readonly WatchSession session;
    private readonly AppearancePreferences preferences;
    private readonly MediaConfiguration media;
    private readonly Action fullscreen;
    private readonly Dispatcher dispatcher = Dispatcher.CurrentDispatcher;
    private CancellationTokenSource? pendingLoadCancellation;
    private PlaybackScope? pendingScope, releasingScope;
    private Task? pendingLoad, releaseTask, checkpointTask, shutdown;
    private bool checkpointRequested, hasWatchActivity, closing;
    internal Action<WatchPlaylist>? OnPlaylistUpdate { get; set; }
    internal Action<string>? OnPlaylistSelection { get; set; }
    internal string? MediaId => session.MediaId;
    public PlaybackWorkspace? Player { get; private set; }
    public bool RuntimeAvailable { get; private set; }
    public bool PlayerPresented => !closing && session.PlayerPresented;
    public WatchPhase Phase => session.Phase;
    public PlaybackScope Scope => session.Scope;
    public long PositionMs => session.PositionMs;
    public string? ErrorKey { get; private set; }
    public event Action? Changed;
    internal Action? OnEndWatching { get; set; }

    public WatchSessionCoordinator(AppearancePreferences preferences, MediaConfiguration media, Action fullscreen)
    {
        this.preferences = preferences; this.media = media;
        this.fullscreen = fullscreen;
        session = new WatchSession(preferences.State);
    }
    public async Task PrepareRuntimeAsync()
    {
        dispatcher.VerifyAccess();
        bool available;
        try { await Task.Run(media.Verify); available = true; }
        catch (Exception error) when (error is not OutOfMemoryException) { available = false; }
        if (closing) return;
        RuntimeAvailable = available;
        if (!available) ErrorKey = "playbackFailed";
        Changed?.Invoke();
    }
    public LibraryPresentation LibraryPresentation(LibraryPresentation source)
    {
        if (closing) return source with { EnabledActions = new HashSet<string>() };
        HashSet<string> enabled = new(source.EnabledActions);
        bool live = source.SelectedId == session.MediaId && Player?.Scope == session.Scope &&
            session.Phase is WatchPhase.Active or WatchPhase.Preparing && Player is not null;
        if (RuntimeAvailable && !preferences.SavingWatchState && !live) enabled.Add("startWatching");
        if (live) { enabled.UnionWith(["continueWatching", "endWatching"]); enabled.ExceptWith(["openLibraryAudio", "openLibrarySubtitles"]); }
        if (preferences.SavingWatchState) enabled.Remove("openSettings");
        var result = source with { EnabledActions = enabled };
        return live ? Player!.LibraryPresentation(result) : result;
    }
    public void Start(LibraryMediaSelection selection, bool requiresSharedAdmission = false, bool autoplay = false)
    {
        dispatcher.VerifyAccess(); if (!RuntimeAvailable || closing) return;
        try
        {
            foreach (var effect in session.Select(selection.Media.Id))
                if (effect is WatchEffect.Release old) Release(old.Scope);
            var effects = session.Start();
            hasWatchActivity = true; ErrorKey = null;
            foreach (var effect in effects)
            {
                if (effect is WatchEffect.Release release) Release(release.Scope);
                else if (effect is WatchEffect.Load load) Load(load.Request, selection, requiresSharedAdmission, autoplay);
            }
            Changed?.Invoke(); RequestCheckpoint();
        }
        catch (Exception error) when (error is WatchException or ContractException) { ErrorKey = "playbackFailed"; Changed?.Invoke(); }
    }
    public void ContinueWatching()
    {
        dispatcher.VerifyAccess(); if (closing || Player?.Scope != session.Scope) return;
        try { session.ContinueWatching(); Changed?.Invoke(); }
        catch (WatchException) { ErrorKey = "playbackFailed"; Changed?.Invoke(); }
    }
    public void ShowLibrary()
    {
        dispatcher.VerifyAccess(); if (closing) return;
        session.ReturnToLibrary(); Changed?.Invoke(); RequestCheckpoint();
    }
    public void End()
    {
        dispatcher.VerifyAccess(); pendingLoadCancellation?.Cancel();
        try { foreach (var effect in session.End()) if (effect is WatchEffect.Release release) Release(release.Scope); }
        catch (Exception error) when (error is WatchException or ContractException)
        { ErrorKey = "playbackFailed"; closing = true; Release(session.Scope); }
        Changed?.Invoke(); RequestCheckpoint();
    }
    public void PreferencesChanged()
    {
        dispatcher.VerifyAccess(); session.SetPreferences(preferences.State.Preferences);
        Player?.SetLanguage(preferences.State.Preferences.Language); Changed?.Invoke();
    }
    public void ClearError() { ErrorKey = null; }
    public Task CloseAsync()
    {
        dispatcher.VerifyAccess(); if (shutdown is not null) return shutdown;
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        shutdown = completion.Task; _ = CloseCoreAsync(completion); return shutdown;
    }
    private async Task CloseCoreAsync(TaskCompletionSource completion)
    {
        try
        {
            closing = true; End();
            var loading = pendingLoad;
            if (releaseTask is not null) await releaseTask;
            if (loading is not null) await loading;
            if (Player is not null) await Player.CloseAsync();
            Player = null;
            if (checkpointTask is not null) await checkpointTask;
            completion.SetResult();
        }
        catch (Exception error) { ErrorKey = "playbackFailed"; Changed?.Invoke(); completion.SetException(error); }
    }
    private void Load(WatchLoad request, LibraryMediaSelection selection, bool requiresSharedAdmission, bool autoplay)
    {
        pendingLoadCancellation?.Cancel(); pendingLoadCancellation = new(); pendingScope = request.Scope;
        pendingLoad = LoadCoreAsync(request, selection, requiresSharedAdmission, autoplay, releaseTask, pendingLoadCancellation.Token);
    }
    private async Task LoadCoreAsync(WatchLoad request, LibraryMediaSelection selection, bool requiresSharedAdmission, bool autoplay, Task? prior, CancellationToken token)
    {
        await Task.Yield();
        try
        {
            if (prior is not null) await prior;
            if (token.IsCancellationRequested || closing || session.Scope != request.Scope || session.Phase != WatchPhase.Preparing) return;
            Player = new PlaybackWorkspace(new(selection.Media.File, media.Package, media.Bridge, null, false),
                preferences.State.Preferences.Language, request.Scope, request.PositionMs, selection,
                Loaded, Observed, action => { if (action == "showLibrary") ShowLibrary(); else if (action == "endWatching") { if (OnEndWatching is { } handler) handler(); else End(); } },
                () => Failed(request.Scope), fullscreen, requiresSharedAdmission, value => OnPlaylistUpdate?.Invoke(value), id => OnPlaylistSelection?.Invoke(id), autoplay, preferences.OpeningMovieVolume, VolumeChanged);
            Changed?.Invoke();
        }
        catch (Exception) { Failed(request.Scope); }
        finally { if (pendingScope == request.Scope) { pendingScope = null; pendingLoad = null; } }
    }
    private void Release(PlaybackScope owner)
    {
        if (pendingScope == owner) pendingLoadCancellation?.Cancel();
        if (Player is not PlaybackWorkspace current || current.Scope != owner || releasingScope == owner) return;
        releasingScope = owner; releaseTask = ReleaseCoreAsync(current, owner);
    }
    private async Task ReleaseCoreAsync(PlaybackWorkspace current, PlaybackScope owner)
    {
        await Task.Yield(); await current.CloseAsync();
        if (ReferenceEquals(Player, current)) Player = null;
        if (releasingScope == owner) releasingScope = null;
        Changed?.Invoke();
    }
    private void Loaded(PlaybackScope owner)
    {
        if (closing || owner != session.Scope || session.Phase != WatchPhase.Preparing) return;
        try { session.Loaded(owner); Changed?.Invoke(); }
        catch (WatchException) { Failed(owner); }
    }
    private void VolumeChanged(PlaybackScope owner, double value)
    {
        dispatcher.VerifyAccess();
        if (closing || owner != session.Scope || session.Phase != WatchPhase.Active || !preferences.RememberMovieVolume(value)) return;
        PreferencesChanged(); RequestCheckpoint();
    }
    private void Observed(PlaybackObservation value)
    {
        if (closing || value.Scope != session.Scope || session.Phase != WatchPhase.Active || value.Status != PlaybackStatus.Ready) return;
        try { session.RecordPlayback(value.Scope, value.PositionMs, value.DurationMs, value.Paused, value.Ended); }
        catch (Exception error) when (error is WatchException or ContractException) { Failed(value.Scope); }
    }
    private void Failed(PlaybackScope owner)
    {
        if (closing || owner != session.Scope) return;
        ErrorKey = "playbackFailed"; End();
    }
    private void RequestCheckpoint()
    {
        if (!hasWatchActivity) return;
        checkpointRequested = true;
        if (checkpointTask is not null) return;
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        checkpointTask = completion.Task; _ = PersistCoreAsync(completion);
    }
    private async Task PersistCoreAsync(TaskCompletionSource completion)
    {
        try
        {
            while (checkpointRequested)
            {
                checkpointRequested = false;
                var write = preferences.PersistWatchStateAsync(session.LocalState); Changed?.Invoke();
                if (!await write && ErrorKey is null) ErrorKey = "saveFailed";
            }
        }
        catch (Exception error) when (error is not OutOfMemoryException) { ErrorKey ??= "saveFailed"; }
        finally { checkpointTask = null; Changed?.Invoke(); completion.TrySetResult(); }
    }
}
