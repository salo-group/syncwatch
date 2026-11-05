package syncwatch

import java.nio.file.Path
import kotlinx.coroutines.*
import syncwatch.domain.FileLocalStateStore
import syncwatch.library.FileLibraryAccessStore
import syncwatch.playback.X11Display

/** One normal desktop window owns its independent library, watch and local-state services. */
internal class ApplicationSession(media: MediaConfiguration, display: X11Display?, private val work: CoroutineScope,
    statePath: Path = AppearancePreferences.defaultPath(), helper: HelperConfiguration = HelperConfiguration.read(emptyArray()),
    accountHelper: Path = Path.of("syncwatch-account-store").toAbsolutePath()) {
    val preferences = AppearancePreferences(FileLocalStateStore(statePath))
    val library = LibraryController(FileLibraryAccessStore(statePath.resolveSibling("library-access.json")), work,
        preferences.state.selectedMediaId, LibraryMetadataReader(media))
    val account = AccountController(work, syncwatch.room.AccountSessionStore(statePath.toAbsolutePath().parent, accountHelper))
    val room = RoomController(work, accountSource = { account.roomAuthority })
    val watch = WatchSessionCoordinator(preferences, media, display, work)
    val shared = RoomWatchCoordinator(room, watch, helper, work, library)
    init {
        library.configureOptions(preferences.state.preferences.scanSubfolders, preferences.state.preferences.automaticRefresh)
        watch.onPlaylistUpdate = shared::updatePlaylist
        watch.onPlaylistSelection = shared::selectPlaylist
        watch.onEndWatching = { work.launch { shared.end() }; Unit }
    }
    private var closing: Job? = null
    fun requestClose(onClosed: () -> Unit) {
        if (closing?.isActive == true) return
        closing = work.launch {
            try { shared.close(); watch.close(); room.close(); account.close(); library.closeAndWait(); onClosed() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* The watch owner exposes the close failure and retains the window. */ }
        }
    }
}
