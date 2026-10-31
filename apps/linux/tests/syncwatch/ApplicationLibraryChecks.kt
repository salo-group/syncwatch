package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.jetbrains.skiko.MainUIDispatcher
import syncwatch.design.LibraryVisualState
import syncwatch.library.FileLibraryAccessStore

fun main(arguments: Array<String>) = runBlocking {
    require(arguments.size <= 1) { "Pass an optional repository directory" }
    val repository = Path.of(arguments.singleOrNull() ?: ".").toAbsolutePath()
    val scratch = Files.createTempDirectory("syncwatch-application-library-")
    val root = Files.createDirectory(scratch.resolve("movies"))
    Files.writeString(root.resolve("A.MKV"), "fixture")
    Files.writeString(root.resolve("B.mp4"), "fixture")
    val empty = Files.createDirectory(scratch.resolve("empty"))
    val store = FileLibraryAccessStore(scratch.resolve("state/library-access.json"))
    val owner = CoroutineScope(MainUIDispatcher + SupervisorJob())
    try {
        helperRuntimeChecks()
        withContext(MainUIDispatcher) {
            watchPlaylistChecks()
            libraryTrackSelectionChecks()
            libraryRefreshChecks(owner)
            libraryMetadataChecks(owner)
            localPlaybackTracksChecks(owner)
            playerPreferencesChecks(scratch)
            watchPersistenceChecks(scratch)
            roomPlaybackGateChecks(owner)
            roomPublicationChecks(repository.resolve("contracts/fixtures/room-client.json"))
            roomPublicationLiveChecks(owner)
            roomWatchContextChecks()
            val model = LibraryController(store, owner, null)
            model.restore(); model.settled()
            check(model.presentation.entries.isEmpty())
            check(model.selectedMedia() == null)
            model.choose(root)
            check(model.presentation.state == LibraryVisualState.scanning)
            check(model.selectedMedia() == null)
            model.settled()
            check(model.errorKey == null && model.presentation.entries.map { it.title } == listOf("A.MKV", "B.mp4"))
            val ids = model.presentation.entries.map { it.id }
            model.select(ids.last())
            val owned = checkNotNull(model.selectedMedia())
            sharedMediaPreparationChecks(owned, owner)
            check(owned.media.id == ids.last() && owned.media.title == "B.mp4")
            model.rescan(); model.settled()
            check(model.presentation.selectedID == ids.last() && model.presentation.entries.map { it.id } == ids)
            val restored = LibraryController(store, owner, ids.last())
            restored.restore(); restored.settled()
            check(restored.presentation.entries.map { it.id } == ids && restored.presentation.selectedID == ids.last())
            model.choose(root.resolve("absent")); model.settled()
            check(model.errorKey == "libraryUnavailable" && model.presentation.entries.map { it.id } == ids)
            model.choose(root); model.choose(root.resolve("absent")); model.settled()
            check(model.presentation.state != LibraryVisualState.scanning && model.presentation.entries.map { it.id } == ids)
            model.choose(root); model.choose(empty); model.settled()
            check(model.errorKey == null && model.presentation.entries.isEmpty())
            check(model.selectedMedia() == null && owned.access.root == root)
            check(store.read().record!!.rootPath == empty.toString())
            val future = "{\"version\":2,\"future\":true}"
            Files.writeString(store.path, future)
            model.choose(root); model.settled()
            check(model.errorKey == "librarySaveFailed" && model.presentation.entries.size == 2)
            check(Files.readString(store.path) == future)
            model.choose(empty); model.close(); model.settled()
            check(model.selectedMedia() == null && model.presentation.enabledActions.isEmpty())
            check(Files.readString(owned.media.file) == "fixture" && owned.access.root == root)
            check(Files.readString(store.path) == future)
            restored.close()
        }
        println("SYNCWATCH_APPLICATION_LIBRARY_OK")
    } finally {
        owner.cancel()
        Files.walk(scratch).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { Files.delete(it) } }
    }
}
