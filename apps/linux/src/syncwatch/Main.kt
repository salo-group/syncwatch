package syncwatch

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.*
import kotlinx.coroutines.launch
import androidx.compose.ui.unit.dp
import androidx.compose.ui.input.key.*
import androidx.compose.ui.window.DialogWindow
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import androidx.compose.ui.window.rememberDialogState
import androidx.compose.ui.window.WindowPlacement
import java.nio.file.Path
import syncwatch.design.*
import syncwatch.domain.*

/** Desktop entry point. Composition, text, dimensions and appearance come from SyncWatch Design.
 * Runtime distribution and visible desktop/provider acceptance remain required.
 */
fun main(arguments: Array<String>) {
    val launch = LocalPlaybackLaunch.read(arguments)
    val media = MediaConfiguration.read(arguments)
    val gallery = arguments.any { it in setOf("--selection-menu-gallery", "--call-gallery", "--player-settings-gallery",
        "--player-gallery", "--settings-gallery", "--design-gallery") }
    // XInitThreads must precede the first toolkit, including the normal Library's folder picker.
    // A missing runtime leaves playback unavailable for this run; it never grants a later unsafe attach.
    val display = when {
        launch != null -> launch.initializeDisplay()
        !gallery -> media.availableDisplay()
        else -> null
    }
    // Return through normal JVM teardown after composition and owned resources
    // finish. Compose's default System.exit bypasses that shutdown boundary.
    application(exitProcessOnExit = false) {
        val scope = rememberCoroutineScope()
        val playback = remember(launch) { launch?.let { PlaybackController(it, requireNotNull(display), scope) } }
        val workspace = remember {
            if (launch == null && !gallery) {
                val index = arguments.indexOf("--state-directory")
                val path = arguments.getOrNull(index + 1)?.takeIf { index >= 0 }
                    ?.let { Path.of(it).toAbsolutePath().normalize().resolve("local-state.json") }
                    ?: AppearancePreferences.defaultPath()
                ApplicationSession(media, display, scope, path, HelperConfiguration.read(arguments))
            } else null
        }
        val state = rememberWindowState(width = SyncWatchTokens.layoutGalleryWidth.dp, height = SyncWatchTokens.layoutGalleryHeight.dp)
        var placementBeforeFullscreen by remember { mutableStateOf(WindowPlacement.Floating) }
        val toggleFullscreen = {
            if (state.placement == WindowPlacement.Fullscreen) state.placement = placementBeforeFullscreen
            else {
                placementBeforeFullscreen = state.placement
                state.placement = WindowPlacement.Fullscreen
            }
        }
        val close = { exitApplication() }
        Window(onCloseRequest = {
            if (playback != null) playback.requestClose(close)
            else if (workspace != null) workspace.requestClose(close) else close()
        }, title = "SyncWatch", state = state, onPreviewKeyEvent = { event ->
            // Retained hidden players yield input. Keep the native window's
            // fullscreen exit available on Library and gallery screens as well.
            if (event.type == KeyEventType.KeyDown && event.key == Key.Escape
                && !event.isAltPressed && !event.isCtrlPressed && !event.isShiftPressed && !event.isMetaPressed
                && playback == null && workspace?.watch?.playerPresented != true && state.placement == WindowPlacement.Fullscreen) {
                state.placement = placementBeforeFullscreen; true
            } else false
        }) {
            if (playback != null || workspace != null) NativePlayerWindowBounds(window)
            when {
                playback != null -> PlaybackWorkspace(playback, close, toggleFullscreen)
                "--selection-menu-gallery" in arguments -> SelectionMenuGallery()
                "--call-gallery" in arguments -> CallGallery()
                "--player-settings-gallery" in arguments -> PlayerSettingsGallery()
                "--player-gallery" in arguments -> PlayerGallery()
                "--settings-gallery" in arguments -> SettingsGallery()
                "--design-gallery" in arguments -> DesignGallery()
                workspace != null -> ApplicationWorkspace(workspace, arguments, { workspace.requestClose(close) }, toggleFullscreen)
            }
            if (playback != null && launch?.report != null) {
                LaunchedEffect(playback) {
                    PlaybackAcceptance.run(playback, window, state, launch.report, launch.detachProbe, close)
                }
            }
        }
    }
}

@Composable private fun ApplicationWorkspace(workspace: ApplicationSession, arguments: Array<String>, onClose: () -> Unit, onFullscreen: () -> Unit) {
    val preferences = workspace.preferences
    val state = preferences.state
    val watch = workspace.watch
    val room = workspace.room
    val shared = workspace.shared
    val roomWork = rememberCoroutineScope()
    var roomOpen by remember { mutableStateOf(false) }
    var settingsOpen by remember { mutableStateOf(false) }
    var editor by remember { mutableStateOf(preferences.editor()) }
    val library = workspace.library
    LaunchedEffect(workspace) {
        library.restore(); watch.prepareRuntime()
        WatchAcceptance.run(arguments, workspace, onClose)
    }
    DisposableEffect(library) { onDispose { library.close() } }
    LaunchedEffect(library.roomRevision) { room.updateLibrary(library.roomRevision, library.roomFiles) }
    LaunchedEffect(room.connected) { shared.membershipChanged() }
    LaunchedEffect(watch.playerPresented) { if (watch.playerPresented) library.dismissTracks() }
    val language = state.preferences.language
    val dark = when (state.preferences.theme) { "dark" -> true; "light" -> false; else -> isSystemInDarkTheme() }
    val theme = if (dark) SyncWatchTheme.dark else SyncWatchTheme.light
    ApplicationChrome(watch.playerPresented, library = {
    LibraryWorkspace(shared.libraryPresentation(watch.libraryPresentation(library.presentation)), language, theme,
        selectionMenu = if (watch.playerPresented) null else library.tracks.menu,
        onSelection = library::selectTrack, onDismissSelection = library::dismissTracks) { action, value ->
        if (action !in setOf("openLibraryAudio", "openLibrarySubtitles")) library.dismissTracks()
        if (action in setOf("openLibraryAudio", "openLibrarySubtitles")) library.openTracks(if (action == "openLibraryAudio") "audio" else "subtitles", language)
        else if (action == "chooseFolder") {
            val picker = javax.swing.JFileChooser().apply {
                fileSelectionMode = javax.swing.JFileChooser.DIRECTORIES_ONLY
                isMultiSelectionEnabled = false
                dialogTitle = SyncWatchCopy.text("folder", language)
                approveButtonText = SyncWatchCopy.text("chooseFolder", language)
            }
            if (picker.showOpenDialog(null) == javax.swing.JFileChooser.APPROVE_OPTION) library.choose(picker.selectedFile.toPath())
        } else if (action == "rescan") library.rescan()
        else if (action == "selectMovie" && value != null) library.select(value)
        else if (action == "startWatching") library.selectedMedia()?.let(shared::start)
        else if (action == "continueWatching") watch.continueWatching()
        else if (action == "endWatching") roomWork.launch { shared.end() }
        else if (action == "openRoom") roomOpen = true
        else if (action == "openSettings") { editor = library.settingsEditor(preferences.editor()); settingsOpen = true }
    }
    }, player = {
        watch.player?.let { controller -> key(controller.scope.clientRunId, controller.scope.mediaGeneration) {
            PlaybackPane(controller, language, theme, onClose, onFullscreen, roomNotice = shared.playerNotice)
        } }
    })
    LaunchedEffect(library.errorKey, watch.errorKey, shared.errorKey) {
        if (WatchAcceptance.requested(arguments)) return@LaunchedEffect
        (library.errorKey ?: watch.errorKey ?: shared.errorKey)?.let { key ->
            javax.swing.JOptionPane.showMessageDialog(null, SyncWatchCopy.text(key, language), SyncWatchCopy.text("brand", language), javax.swing.JOptionPane.WARNING_MESSAGE)
            library.clearError(); watch.clearError(); shared.clearError()
        }
    }
    if (roomOpen) {
        fun closeDialog() { if (room.editor.busy) roomWork.launch { room.close(); roomOpen = false } else { room.editor = room.editor.clearSecrets(); roomOpen = false } }
        DialogWindow(onCloseRequest = ::closeDialog, title = SyncWatchCopy.text("openRoom", language),
            state = rememberDialogState(width = SyncWatchTokens.layoutSettingsWidth.dp, height = SyncWatchTokens.layoutSettingsHeight.dp)) {
            RoomDialog(room.editor, { room.editor = it }, language, theme) { action -> when (action) {
                "connectRoom" -> room.connect()
                "leaveRoom" -> roomWork.launch { shared.leave() }
                "pasteInvitation" -> room.pasteInvitation(runCatching {
                    java.awt.Toolkit.getDefaultToolkit().systemClipboard.getData(java.awt.datatransfer.DataFlavor.stringFlavor) as? String
                }.getOrNull() ?: "")
                "copyInvitation" -> room.invitationToCopy()?.let { value ->
                    val copied = try { java.awt.Toolkit.getDefaultToolkit().systemClipboard.setContents(java.awt.datatransfer.StringSelection(value), null); true }
                        catch (_: IllegalStateException) { false }
                        catch (_: SecurityException) { false }
                    room.invitationCopyCompleted(copied)
                }
                "close" -> closeDialog()
            } }
        }
    }
    if (settingsOpen) {
        DialogWindow(onCloseRequest = { settingsOpen = false }, title = SyncWatchCopy.text("openSettings", language),
            state = rememberDialogState(width = SyncWatchTokens.layoutSettingsWidth.dp, height = SyncWatchTokens.layoutSettingsHeight.dp)) {
            SettingsDialog(editor, { editor = it }, language = language, theme = theme,
                onSave = { values ->
                    val options = "scanSubfolders" in editor.editableFields
                    if (options && !library.canConfigure) false else preferences.save(values, options).also { saved ->
                        if (saved) {
                            library.configureOptions(preferences.state.preferences.scanSubfolders, preferences.state.preferences.automaticRefresh)
                            watch.preferencesChanged()
                        }
                    }
                },
                onClose = { settingsOpen = false })
        }
    }
}

/** Only local appearance facts are enabled. Provider capabilities require their own observed owners. */
internal class AppearancePreferences(store: LocalStateStore) {
    private val store = SerializedLocalStateStore(store)
    var state by mutableStateOf(LocalState())
        private set
    var savingWatchState by mutableStateOf(false)
        private set
    private var canWrite = false
    init {
        try {
            val loaded = this.store.read()
            when (loaded.status) {
                LocalStateStatus.loaded -> { state = loaded.state!!; canWrite = true }
                LocalStateStatus.missing, LocalStateStatus.invalid -> canWrite = true
                LocalStateStatus.unsupportedVersion -> Unit
            }
        } catch (_: java.io.IOException) { }
    }
    fun editor() = SettingsEditor(values = mapOf("theme" to state.preferences.theme, "language" to state.preferences.language),
        editableFields = if (canWrite && !savingWatchState) setOf("theme", "language") else emptySet(), canSave = canWrite && !savingWatchState)
    fun save(values: Map<String, String>, libraryOptions: Boolean = false): Boolean {
        if (!canWrite || savingWatchState) return false
        val theme = values["theme"] ?: return false; val language = values["language"] ?: return false
        var updated = state.preferences.copy(theme = theme, language = language)
        if (libraryOptions) {
            val scan = values["scanSubfolders"]?.toBooleanStrictOrNull() ?: return false
            val refresh = values["automaticRefresh"]?.toBooleanStrictOrNull() ?: return false
            updated = updated.copy(scanSubfolders = scan, automaticRefresh = refresh)
        }
        val next = state.copy(preferences = updated)
        return try { next.validate(); store.write(next); state = next; true }
        catch (_: java.io.IOException) { false }
        catch (_: WatchFailure) { false }
    }
    suspend fun persistWatchState(value: LocalState): Boolean {
        if (savingWatchState) return false
        val next = value.copy(preferences = state.preferences)
        try { next.validate() } catch (_: WatchFailure) { return false }
        state = next
        if (!canWrite) return false
        savingWatchState = true
        return try {
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { store.write(next) }
            true
        } catch (_: java.io.IOException) { false }
        catch (_: WatchFailure) { false }
        finally { savingWatchState = false }
    }
    companion object {
        fun defaultPath(): Path {
            val directory = System.getenv("XDG_STATE_HOME")?.takeIf { Path.of(it).isAbsolute }
                ?.let(Path::of) ?: Path.of(System.getProperty("user.home"), ".local", "state")
            return directory.resolve("SyncWatch/local-state.json")
        }
    }
}
