using System.IO;
using System.Windows;
using System.Windows.Controls;
using SyncWatch.Bridge;
using SyncWatch.Design;
using SyncWatch.Domain;
using SyncWatch.Playback;

namespace SyncWatch;

internal sealed record LocalPlaybackLaunch(string File, string Package, string Bridge, string? Report, bool DetachProbe)
{
    public static LocalPlaybackLaunch? Read(string[] arguments)
    {
        string? Value(string flag) { int i = Array.IndexOf(arguments, flag); return i >= 0 && i + 1 < arguments.Length ? arguments[i + 1] : null; }
        string? file = Value("--local-playback");
        var media = MediaConfiguration.Read(arguments);
        return file is null ? null : new(Path.GetFullPath(file),
            media.Package, media.Bridge,
            Value("--playback-report"), arguments.Contains("--playback-detach-probe"));
    }
}

// Explicit local development entry. Normal room/watch capabilities remain gated.
internal sealed class PlaybackWorkspace : UserControl, IAsyncDisposable
{
    private readonly VLCPlayback player = new();
    private readonly PlaybackScope scope;
    private readonly PlayerChrome chrome = new();
    private readonly LocalPlaybackTracks tracks;
    private readonly WatchPlaylist playlist = new();
    private bool autoplayPending;
    private readonly Action<WatchPlaylist>? onPlaylistUpdate;
    private readonly Action<string>? onPlaylistSelection;
    private readonly NativeVideoHost surface;
    private readonly LocalPlaybackLaunch launch;
    private string language;
    private readonly long initialPositionMs;
    private long sharedResumePositionMs;
    private LibraryMediaSelection? selection;
    private readonly Action<PlaybackScope>? onLoaded;
    private readonly Action<PlaybackObservation>? onObservation;
    private readonly Action<string>? onNavigation;
    private readonly Action? onFailure;
    private readonly Action? onFullscreen;
    private HashSet<string> NavigationActions => onNavigation is null ? ["endWatching", "toggleFullscreen"] : ["endWatching", "toggleFullscreen", "showLibrary"];
    private readonly CancellationTokenSource cancellation = new();
    private PlayerPresentation presentation = new();
    private string roomNotice = "";
    private void RenderChrome()
    {
        chrome.SelectionPresentation = playlist.Menu ?? tracks.Menu;
        chrome.Render(playlist.Presentation(presentation) with { RoomNotice = roomNotice }, language);
    }
    private void RefreshPlaylist() { onPlaylistUpdate?.Invoke(playlist); presentation = playlist.Presentation(presentation); }
    private void SelectMenu(SelectionMenuRequest request)
    {
        RefreshPlaylist();
        if (request.Kind == "playlist") { if (playlist.Selection(request) is { } id) onPlaylistSelection?.Invoke(id); }
        else tracks.Select(request);
        RenderChrome();
    }
    private void DismissMenu()
    {
        playlist.Dismiss(); tracks.DismissMenu();
        RenderChrome();
    }
    internal void SetRoomNotice(string value)
    {
        if (roomNotice == value) return;
        roomNotice = value; RenderChrome();
    }
    private Task? polling, shutdown;
    private HelperSession? sharedSession;
    private bool requiresSharedAdmission;
    private SharedMediaPreparation sharedMediaPreparation = new();
    private Func<bool>? sharedAuthority;
    internal HelperSessionState? SharedState { get; private set; }
    private bool closing;
    private bool attached;
    private WindowFullscreen? standaloneFullscreen;
    internal VLCPlayback Player => player;
    internal PlaybackScope Scope => scope;
    internal NativeVideoHost Surface => surface;
    internal void RequestAction(string action, double? value = null) => Action(action, value);
    internal async Task<MediaDescriptor> PrepareSharedMediaAsync(CancellationToken token = default)
    {
        if (closing || selection is null) throw new InvalidOperationException("media-unavailable");
        var descriptor = await sharedMediaPreparation.PrepareAsync(selection, player, scope, token);
        if (closing) throw new OperationCanceledException();
        sharedResumePositionMs = Math.Min(initialPositionMs, descriptor.DurationMs!.Value);
        return descriptor;
    }
    internal void RequireSharedAdmission() { requiresSharedAdmission = true; GateSharedActions(); RenderChrome(); }
    internal void AdmitSharedPlayback(bool autoplay = false)
    {
        if (sharedSession is null || SharedState is not { Connection: HelperConnection.Connected, PlaybackReady: true } || sharedAuthority?.Invoke() == false)
            throw new InvalidOperationException("Shared playback unavailable");
        // A user's Pause can clear Syncplay readiness without revoking the admitted file.
        requiresSharedAdmission = false;
        if (autoplay) sharedSession.PlayFrom(sharedResumePositionMs);
    }
    internal async Task RevokeSharedAsync(bool releaseAdmission = false)
    {
        RequireSharedAdmission(); sharedAuthority = null;
        var session = sharedSession; sharedSession = null; SharedState = null;
        var preparation = sharedMediaPreparation;
        await preparation.CloseAsync();
        if (session is not null) await session.DisposeAsync();
        try { await player.SetPausedAsync(true, scope); } catch (Exception) { }
        if (closing) return;
        sharedMediaPreparation = new(); requiresSharedAdmission = !releaseAdmission;
    }
    // Transfer only after the room coordinator admits the selected local media.
    internal async Task ConnectSharedAsync(IHelperTransport transport, SyncplayEndpoint endpoint, SharedMedia media, string revision, bool requireDeclaredIdentity = false, Func<bool>? authority = null)
    {
        try
        {
            if (closing || !attached || sharedSession is not null || authority?.Invoke() == false) throw new InvalidOperationException("Shared playback unavailable");
            sharedAuthority = authority;
            await sharedMediaPreparation.ValidateAsync(player, scope);
            if (closing || !attached || sharedSession is not null || authority?.Invoke() == false) throw new InvalidOperationException("Shared playback unavailable");
            var session = new HelperSession(transport, player, scope, endpoint, media, revision, state =>
            { SharedState = state; GateSharedActions(); RenderChrome(); }, requiresSharedAdmission || requireDeclaredIdentity);
            sharedSession = session; SharedState = session.State; GateSharedActions(); RenderChrome();
            session.Start();
        }
        catch { await transport.DisposeAsync(); throw; }
    }
    private void GateSharedActions()
    {
        if ((requiresSharedAdmission || sharedSession is not null) &&
            (sharedAuthority?.Invoke() == false || SharedState is not { Connection: HelperConnection.Connected } || (requiresSharedAdmission && SharedState is not { PlaybackReady: true })))
            presentation = presentation with { EnabledActions = presentation.EnabledActions.Except(["playPause", "seek", "skip"]).ToHashSet() };
    }
    internal void DetachSurface()
    {
        chrome.VideoSurface = null;
        RenderChrome();
    }

    public PlaybackWorkspace(LocalPlaybackLaunch launch, string language, PlaybackScope? scope = null, long positionMs = 0,
        LibraryMediaSelection? selection = null, Action<PlaybackScope>? onLoaded = null,
        Action<PlaybackObservation>? onObservation = null, Action<string>? onNavigation = null, Action? onFailure = null,
        Action? onFullscreen = null, bool requiresSharedAdmission = false, Action<WatchPlaylist>? onPlaylistUpdate = null, Action<string>? onPlaylistSelection = null, bool autoplay = false)
    {
        this.launch = launch; this.language = language;
        this.onPlaylistUpdate = onPlaylistUpdate; this.onPlaylistSelection = onPlaylistSelection;
        this.scope = scope ?? PlaybackScope.Create().ReplacingMedia(); initialPositionMs = positionMs;
        tracks = new(player, this.scope, RenderTracks, new ExternalTrackPicker(() => Window.GetWindow(this)));
        this.selection = selection; this.onLoaded = onLoaded; this.onObservation = onObservation;
        this.onNavigation = onNavigation; this.onFailure = onFailure;
        this.onFullscreen = onFullscreen;
        this.requiresSharedAdmission = requiresSharedAdmission;
        autoplayPending = autoplay && !requiresSharedAdmission;
        surface = new NativeVideoHost(CloseAsync);
        surface.SurfaceCreated += handle => { if (!attached && !cancellation.IsCancellationRequested) { attached = true; polling = PollAsync(handle); } };
        chrome.VideoSurface = surface;
        chrome.ActionRequested += Action;
        chrome.SelectionRequested += SelectMenu;
        chrome.SelectionDismissed += DismissMenu;
        presentation = presentation with { Title = Path.GetFileName(launch.File), EnabledActions = NavigationActions };
        RenderChrome(); Content = chrome;
    }
    public void SetLanguage(string value) { language = value; RenderChrome(); }
    internal LibraryPresentation LibraryPresentation(LibraryPresentation source) => tracks.LibraryPresentation(source);
    private void RenderTracks() => RenderChrome();
    private async Task PollAsync(nint handle)
    {
        try
        {
            await player.OpenAsync(launch.Bridge, launch.Package, handle, cancellation.Token);
            await player.LoadAsync(launch.File, scope, cancellation.Token);
            bool admissionPending = true;
            bool resumePending = initialPositionMs > 0;
            if (launch.Report is string report) _ = PlaybackAcceptance.RunAsync(this, report, launch.DetachProbe);
            while (!cancellation.IsCancellationRequested)
            {
                PlaybackObservation value = await player.ObserveAsync(scope, cancellation.Token);
                if (closing || cancellation.IsCancellationRequested) break;
                if (value.Status == PlaybackStatus.Failed) throw new PlaybackException("media");
                // Restore the local position before track acknowledgement and admission.
                if (resumePending && value.Status == PlaybackStatus.Ready)
                {
                    resumePending = false;
                    if (value.Seekable)
                    {
                        try { await player.SeekAsync(Math.Min(initialPositionMs, value.DurationMs ?? initialPositionMs), scope, cancellation.Token); }
                        catch (PlaybackException) { /* The next actual observation remains authoritative. */ }
                        continue;
                    }
                }
                if (admissionPending && value.Status == PlaybackStatus.Ready)
                {
                    await tracks.ApplyInitialAsync(selection?.Tracks ?? new(), cancellation.Token);
                    cancellation.Token.ThrowIfCancellationRequested();
                    if (closing) break;
                    admissionPending = false; onLoaded?.Invoke(scope);
                    continue;
                }
                HashSet<string> enabled = NavigationActions; enabled.Add("playPause");
                if (value.Seekable) enabled.UnionWith(["seek", "skip"]);
                if (value.Volume is not null) enabled.UnionWith(["setMovieVolume", "muteMovie"]);
                if (value.Status == PlaybackStatus.Ready && !value.Ended) enabled.UnionWith(["selectAudio", "selectSubtitles"]);
                else tracks.Dismiss();
                presentation = presentation with
                {
                    State = value.Ended ? PlayerVisualState.Ended : value.Buffering ? PlayerVisualState.Buffering : value.Status != PlaybackStatus.Ready ? PlayerVisualState.Loading : value.Paused ? PlayerVisualState.Paused : PlayerVisualState.Playing,
                    PositionMs = value.PositionMs,
                    DurationMs = value.DurationMs ?? 0,
                    Volume = value.Volume ?? presentation.Volume,
                    Muted = value.Muted,
                    EnabledActions = enabled,
                };
                GateSharedActions(); RefreshPlaylist(); RenderChrome();
                onObservation?.Invoke(value);
                if (autoplayPending && value.Status == PlaybackStatus.Ready && !value.Ended)
                {
                    autoplayPending = false;
                    await player.SetPausedAsync(false, scope, cancellation.Token);
                }
                await Task.Delay(100, cancellation.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (ObjectDisposedException) when (cancellation.IsCancellationRequested) { }
        catch (Exception)
        {
            if (!cancellation.IsCancellationRequested)
            {
                tracks.Dismiss();
                presentation = presentation with { EnabledActions = new HashSet<string> { "endWatching" } };
                RenderChrome();
                if (launch.Report is string report)
                {
                    await player.DisposeAsync();
                    PlaybackAcceptance.Finish(report, new() { ["success"] = false, ["failure"] = "startup", ["physicalOutputAccepted"] = false });
                    return;
                }
                if (onFailure is not null) onFailure();
                else MessageBox.Show(Window.GetWindow(this), SyncWatchCopy.Text("playbackFailed", language), "SyncWatch", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }
    }
    private async void Action(string action, double? value)
    {
        if (closing || cancellation.IsCancellationRequested) return;
        GateSharedActions(); RefreshPlaylist();
        if (!presentation.EnabledActions.Contains(action)) return;
        if (action == "openPlaylist") { tracks.Dismiss(); playlist.Open(); RenderChrome(); return; }
        if (action is "previousFile" or "nextFile")
        {
            if (playlist.Neighbor(action == "previousFile" ? -1 : 1) is { } id) onPlaylistSelection?.Invoke(id);
            return;
        }
        playlist.Dismiss();
        if (action is "selectAudio" or "selectSubtitles") { tracks.Open(action == "selectAudio" ? "audio" : "subtitles", language); return; }
        if (action is "showLibrary" or "endWatching" or "toggleFullscreen") tracks.Dismiss();
        if ((action is "endWatching" or "showLibrary") && onNavigation is not null) { onNavigation(action); return; }
        if (action == "endWatching")
        {
            Window window = Window.GetWindow(this);
            await CloseAsync(); window.Close(); return;
        }
        if (action == "toggleFullscreen")
        {
            if (onFullscreen is not null) { onFullscreen(); return; }
            (standaloneFullscreen ??= new WindowFullscreen(Window.GetWindow(this))).Toggle();
            return;
        }
        try
        {
            if (sharedSession is not null && action is "playPause" or "seek")
            {
                if (action == "playPause") sharedSession.TogglePause();
                else if (value is double position) sharedSession.Seek(checked((long)position));
                return;
            }
            switch (action)
            {
                case "playPause": await player.SetPausedAsync(presentation.Playing, scope, cancellation.Token); break;
                case "seek" when value is double seek: await player.SeekAsync(checked((long)seek), scope, cancellation.Token); break;
                case "setMovieVolume" when value is double volume: await player.SetVolumeAsync(volume, presentation.Muted, scope, cancellation.Token); break;
                case "muteMovie": await player.SetVolumeAsync(presentation.Volume, !presentation.Muted, scope, cancellation.Token); break;
            }
        }
        catch (Exception) { /* A rejected request never changes the observation. */ }
    }
    public Task CloseAsync() => shutdown ??= CloseCoreAsync();
    private async Task CloseCoreAsync()
    {
        closing = true; playlist.Close();
        cancellation.Cancel();
        await tracks.CloseAsync();
        presentation = presentation with { EnabledActions = new HashSet<string>() };
        RenderChrome();
        await sharedMediaPreparation.CloseAsync();
        try { if (sharedSession is not null) await sharedSession.DisposeAsync(); }
        finally
        {
            sharedSession = null;
            try { await player.DisposeAsync(); }
            finally { tracks.ReleaseExternalAccess(); }
        }
        if (polling is not null) await polling;
        GC.KeepAlive(selection);
        selection = null;
    }
    public async ValueTask DisposeAsync() => await CloseAsync();
}
