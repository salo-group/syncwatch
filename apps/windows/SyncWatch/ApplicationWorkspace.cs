using System.IO;
using System.Windows;
using System.Windows.Controls;

namespace SyncWatch;

// Bind application-owned services to common components. No screen layout is authored here.
internal sealed class ApplicationWorkspace : UserControl
{
    private readonly Window owner;
    private readonly App application;
    private readonly AppearancePreferences preferences;
    private readonly Design.LibraryWorkspace library = new();
    private readonly LibraryController catalog;
    private readonly WatchSessionCoordinator watch;
    private readonly RoomController room = new();
    private readonly RoomWatchCoordinator shared;
    private readonly Design.ApplicationChrome chrome;
    private bool started;

    public ApplicationWorkspace(Window owner, App application)
    {
        this.owner = owner; this.application = application;
        preferences = application.Preferences!;
        catalog = new LibraryController(new Library.FileLibraryAccessStore(
            Path.Combine(Path.GetDirectoryName(application.StatePath)!, "library-access.json")), Dispatcher, preferences.State.SelectedMediaId, new LibraryMetadataReader(application.Media!));
        catalog.ConfigureOptions(preferences.State.Preferences.ScanSubfolders, preferences.State.Preferences.AutomaticRefresh);
        var fullscreen = new WindowFullscreen(owner);
        watch = new WatchSessionCoordinator(preferences, application.Media!, fullscreen.Toggle);
        shared = new(room, watch, HelperConfiguration.Read(Environment.GetCommandLineArgs()), catalog);
        watch.OnPlaylistUpdate = shared.UpdatePlaylist;
        watch.OnPlaylistSelection = shared.SelectPlaylist;
        watch.OnEndWatching = async () => await shared.EndAsync();
        chrome = new Design.ApplicationChrome(library); Content = chrome;
        catalog.Changed += Refresh; watch.Changed += Refresh; room.Changed += Refresh;
        shared.Changed += Refresh;
        library.ActionRequested += Action;
        library.SelectionRequested += catalog.SelectTrack; library.SelectionDismissed += catalog.DismissTracks;
        Loaded += async (_, _) =>
        {
            if (started) return; started = true;
            catalog.Restore(); await watch.PrepareRuntimeAsync();
            await WatchAcceptance.RunAsync(owner, preferences, catalog, watch);
        };
        Refresh();
    }
    public async Task CloseAsync() { await shared.CloseAsync(); await watch.CloseAsync(); await room.CloseAsync(); await catalog.CloseAsync(); }
    private void Refresh()
    {
        room.UpdateLibrary(catalog.RoomRevision, () => catalog.RoomFiles);
        shared.MembershipChanged();
        if (watch.PlayerPresented) catalog.DismissTracks();
        library.SelectionPresentation = watch.PlayerPresented ? null : catalog.Tracks.Menu;
        library.Render(shared.LibraryPresentation(watch.LibraryPresentation(catalog.Presentation)), preferences.State.Preferences.Language);
        watch.Player?.SetRoomNotice(shared.PlayerNotice);
        chrome.SetPlayer(watch.Player); chrome.PresentPlayer(watch.PlayerPresented);
        if (WatchAcceptance.Requested) return;
        if ((catalog.ErrorKey ?? watch.ErrorKey ?? shared.ErrorKey) is not string key) return;
        catalog.ClearError(); watch.ClearError(); shared.ClearError();
        Dispatcher.InvokeAsync(() =>
        {
            if (IsLoaded) MessageBox.Show(owner, Design.SyncWatchCopy.Text(key, preferences.State.Preferences.Language),
                "SyncWatch", MessageBoxButton.OK, MessageBoxImage.Warning);
        });
    }
    private void Action(string action, string? value)
    {
        if (action is not ("openLibraryAudio" or "openLibrarySubtitles")) catalog.DismissTracks();
        if (action is "openLibraryAudio" or "openLibrarySubtitles") catalog.OpenTracks(action == "openLibraryAudio" ? "audio" : "subtitles", preferences.State.Preferences.Language);
        else if (action == "chooseFolder")
        {
            ChooseFolder(owner);
        }
        else if (action == "rescan") catalog.Rescan();
        else if (action == "selectMovie" && value is not null) catalog.Select(value);
        else if (action == "startWatching" && catalog.SelectedMedia() is LibraryMediaSelection selection) shared.Start(selection);
        else if (action == "continueWatching") watch.ContinueWatching();
        else if (action == "endWatching") _ = shared.EndAsync();
        else if (action == "openRoom") OpenRoom();
        else if (action == "openSettings") OpenSettings();
    }
    private void OpenRoom()
    {
        var view = new Design.RoomDialog(room.Editor, preferences.State.Preferences.Language);
        var window = CreateDialog(view); bool released = false;
        room.Changed += view.Refresh;
        view.ActionRequested += async action =>
        {
            if (action == "connectRoom") room.Connect();
            else if (action == "leaveRoom") await shared.LeaveAsync();
            else if (action == "pasteInvitation")
            {
                try { room.PasteInvitation(Clipboard.GetText()); }
                catch (System.Runtime.InteropServices.ExternalException) { room.PasteInvitation(""); }
            }
            else if (action == "copyInvitation" && room.InvitationToCopy() is string invitation)
            {
                bool copied;
                try { Clipboard.SetText(invitation); copied = true; }
                catch (System.Runtime.InteropServices.ExternalException) { copied = false; }
                room.InvitationCopyCompleted(copied);
            }
            else if (action == "close") { if (room.Editor.Busy) await room.CloseAsync(); released = true; window.Close(); }
        };
        window.Closing += async (_, e) =>
        {
            if (!released && room.Editor.Busy) { e.Cancel = true; await room.CloseAsync(); released = true; window.Close(); }
        };
        try { window.ShowDialog(); }
        finally { room.Changed -= view.Refresh; room.Editor.ClearSecrets(); }
    }
    private Window CreateDialog(UserControl content) => new()
    {
        Content = content,
        Owner = owner,
        Title = "SyncWatch",
        WindowStyle = WindowStyle.None,
        AllowsTransparency = true,
        Background = System.Windows.Media.Brushes.Transparent,
        ResizeMode = ResizeMode.NoResize,
        SizeToContent = SizeToContent.WidthAndHeight,
        ShowInTaskbar = false,
        WindowStartupLocation = WindowStartupLocation.CenterOwner
    };
    private void ChooseFolder(Window parent)
    {
        if (!catalog.CanConfigure) return;
        var picker = new Microsoft.Win32.OpenFolderDialog { Title = Design.SyncWatchCopy.Text("folder", preferences.State.Preferences.Language), Multiselect = false };
        if (picker.ShowDialog(parent) == true) catalog.Choose(picker.FolderName);
    }
    private void OpenSettings()
    {
        var view = new Design.SettingsDialog(catalog.SettingsEditor(preferences.CreateEditor()), preferences.State.Preferences.Language);
        var window = CreateDialog(view);
        view.CloseRequested += window.Close; view.MoveRequested += window.DragMove;
        void RefreshFolder() => view.UpdateFolder(catalog.FolderPath, catalog.CanConfigure);
        view.ChooseFolderRequested += () => ChooseFolder(window);
        catalog.Changed += RefreshFolder;
        RefreshFolder();
        view.Persist = values =>
        {
            bool options = view.Editor.EditableFields.Contains("scanSubfolders");
            if (options && !catalog.CanConfigure) return false;
            if (!preferences.Save(values, options)) return false;
            catalog.ConfigureOptions(preferences.State.Preferences.ScanSubfolders, preferences.State.Preferences.AutomaticRefresh);
            application.ApplySystemTheme(); watch.PreferencesChanged(); Refresh();
            view.SetLanguage(preferences.State.Preferences.Language); return true;
        };
        try { window.ShowDialog(); }
        finally { catalog.Changed -= RefreshFolder; }
    }
}
