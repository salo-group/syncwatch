package syncwatch

import androidx.compose.runtime.*
import java.nio.file.Path
import syncwatch.design.*
import syncwatch.domain.*

/** Serializes local preferences with watch checkpoints; provider capabilities keep their own owners. */
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
    fun playerSettingsEditor() = PlayerSettingsEditor(
        values = mapOf("skipSeconds" to state.preferences.skipSeconds.toString()),
        editableFields = if (canWrite && !savingWatchState) setOf("skipSeconds") else emptySet(),
        canSave = canWrite && !savingWatchState)
    fun savePlayerSettings(values: Map<String, String>): Boolean {
        if (!canWrite || savingWatchState) return false
        val value = values["skipSeconds"] ?: return false
        val field = SyncWatchPlayerSettings.fields.single { it.id == "skipSeconds" }
        if (!PlayerSettingsEditor.valid(value, field)) return false
        val seconds = value.toIntOrNull() ?: return false
        val next = state.copy(preferences = state.preferences.copy(skipSeconds = seconds))
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
