package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import syncwatch.domain.*

private class GatedDiskStore(path: Path) : LocalStateStore {
    private val disk = FileLocalStateStore(path)
    val entered = CountDownLatch(1)
    val proceed = CountDownLatch(1)
    private var first = true
    override fun read() = disk.read()
    override fun write(state: LocalState) {
        if (first) {
            first = false
            entered.countDown()
            check(proceed.await(15, TimeUnit.SECONDS)) { "checkpoint worker timeout" }
        }
        disk.write(state)
    }
}

internal suspend fun watchPersistenceChecks(scratch: Path) = coroutineScope {
    val path = scratch.resolve("watch/local-state.json")
    val disk = FileLocalStateStore(path)
    val gate = GatedDiskStore(path)
    val preferences = AppearancePreferences(gate)
    val snapshot = LocalState(selectedMediaId = "movie-1", resumePositions = listOf(ResumePosition("movie-1", 1234)))
    val writing = async { preferences.persistWatchState(snapshot) }
    try {
        check(withContext(Dispatchers.IO) { gate.entered.await(15, TimeUnit.SECONDS) })
        check(preferences.savingWatchState && !preferences.editor().canSave)
        check(!preferences.save(mapOf("theme" to "dark", "language" to "ru")))
        check(!preferences.playerSettingsEditor().canSave && !preferences.savePlayerSettings(mapOf("skipSeconds" to "30")))
        check(!preferences.persistWatchState(snapshot))
        gate.proceed.countDown()
        check(writing.await() && !preferences.savingWatchState)
        check(preferences.save(mapOf("theme" to "dark", "language" to "ru")))
        check(preferences.persistWatchState(snapshot))
        val restored = AppearancePreferences(disk).state
        check(restored.preferences.theme == "dark" && restored.preferences.language == "ru")
        check(restored.selectedMediaId == snapshot.selectedMediaId && restored.resumePositions == snapshot.resumePositions)
        val future = "{\"version\":2,\"future\":true}"
        Files.writeString(path, future)
        check(!preferences.persistWatchState(snapshot) && Files.readString(path) == future)
        val fresh = AppearancePreferences(disk)
        check(!fresh.persistWatchState(snapshot))
        check(!fresh.save(mapOf("theme" to "light", "language" to "en")) && Files.readString(path) == future)
        println("SYNCWATCH_WATCH_PERSISTENCE_OK")
    } finally { gate.proceed.countDown() }
}
