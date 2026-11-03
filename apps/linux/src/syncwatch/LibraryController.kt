package syncwatch

import syncwatch.design.SyncWatchSelectionMenu
import syncwatch.design.SettingsEditor
import syncwatch.design.SyncWatchSettings

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.nio.file.Path
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import syncwatch.design.LibraryEntry
import syncwatch.design.LibraryPresentation
import syncwatch.design.LibraryVisualState
import syncwatch.library.*

/** Local selection ownership only; the path-based catalog ID is not peer content identity. */
internal data class LibraryMediaSelection(val media: LocalMediaFile, val access: LibraryFolderAccess, val tracks: LocalTrackPreferences = LocalTrackPreferences())

/** UI coordination only. The library services and shared design own their respective boundaries. */
internal class LibraryController(private val store: LibraryAccessStore, parent: CoroutineScope, private val savedSelection: String?, private val metadata: syncwatch.library.MediaMetadataReader? = null) {
    private val scope = CoroutineScope(parent.coroutineContext + SupervisorJob(parent.coroutineContext[Job]))
    private val persistence = Mutex()
    private val metadataSerial = Mutex()
    private val scanner = FileSystemLibrary(SyncWatchSelectionMenu.externalFileFilters.flatMap { filter ->
        filter.extensions.map { it to (if (filter.kind == "audio") syncwatch.library.LibraryCompanionKind.audio else syncwatch.library.LibraryCompanionKind.subtitles) }
    }.toMap())
    private val durations = LibraryDurationCache()
    private var pending: Job? = null
    private var monitor: LibraryChangeMonitor? = null
    private var monitorAccess: LibraryFolderAccess? = null
    private var monitorRecursive: Boolean? = null
    private var configuredSubfolders: Boolean? = null
    private var configuredRefresh: Boolean? = null
    private var monitorJob: Job? = null
    private var metadataTask: Job? = null
    private var metadataGeneration = 0L
    private var grant: LibraryFolderAccess? = null
    private var catalog = emptyMap<String, LocalMediaFile>()
    private var stable: LibraryPresentation? = null
    private var generation = 0L
    private var closed = false
    var tracks by mutableStateOf(LibraryTrackSelection())
        private set
    var presentation by mutableStateOf(LibraryPresentation(enabledActions = setOf("chooseFolder", "openSettings")))
        private set
    var roomRevision by mutableStateOf(java.util.UUID.randomUUID())
        private set
    val roomFiles: List<LocalMediaFile> get() = catalog.values.sortedBy { it.id }
    var errorKey by mutableStateOf<String?>(null)
        private set
    fun restore() = start {
        val value = storage { store.read() }
        when (value.status) {
                LibraryAccessStatus.loaded -> withContext(Dispatchers.IO) { LibraryFolderAccess.restore(requireNotNull(value.record)) }
                LibraryAccessStatus.missing, LibraryAccessStatus.invalid -> null
                LibraryAccessStatus.unsupportedVersion -> error("unsupported-library-version")
        }
    }
    fun choose(path: Path) {
        val recursive = scanRecursively(grant); val refresh = refreshAutomatically(grant)
        start { withContext(Dispatchers.IO) { LibraryFolderAccess.selected(path, recursive, refresh) } }
    }
    fun rescan() { grant?.let { current -> start(persistAccess = false) { current } } }
    val folderPath: String get() = grant?.root?.toString().orEmpty()
    val canConfigure: Boolean get() = !closed && presentation.state != LibraryVisualState.scanning
    fun settingsEditor(base: SettingsEditor) = SettingsEditor(
        values = base.values + mapOf("scanSubfolders" to scanRecursively(grant).toString(), "automaticRefresh" to refreshAutomatically(grant).toString()),
        editableFields = base.editableFields + if (canConfigure && base.canSave) setOf("scanSubfolders", "automaticRefresh") else emptySet(), canSave = base.canSave)
    fun configureOptions(scanSubfolders: Boolean?, automaticRefresh: Boolean?): Boolean {
        if (configuredSubfolders == scanSubfolders && configuredRefresh == automaticRefresh) return !closed
        if (!canConfigure) return false
        configuredSubfolders = scanSubfolders; configuredRefresh = automaticRefresh; rescan(); return true
    }
    private fun scanRecursively(access: LibraryFolderAccess?) = configuredSubfolders ?: access?.record?.scanSubfolders ?: (SyncWatchSettings.defaults["scanSubfolders"] == "true")
    private fun refreshAutomatically(access: LibraryFolderAccess?) = configuredRefresh ?: access?.record?.autoRefresh ?: (SyncWatchSettings.defaults["automaticRefresh"] == "true")
    private fun sameRoomCatalog(files: List<LocalMediaFile>) = files.size == catalog.size && files.all { file ->
        val old = catalog[file.id]
        old != null && file.title == old.title && file.relativePath == old.relativePath &&
            file.sizeBytes == old.sizeBytes && file.modificationTime == old.modificationTime
    }
    fun select(id: String) {
        if (!closed) presentation.dispatch("selectMovie", id) { _, _ -> presentation = presentation.copy(selectedID = id); startMetadata() }
    }
    fun selectedMedia(): LibraryMediaSelection? {
        check(java.awt.EventQueue.isDispatchThread()) { "library-event-thread" }
        if (closed || presentation.state != LibraryVisualState.selected) return null
        val media = catalog[presentation.selectedID] ?: return null
        return LibraryMediaSelection(media, grant ?: return null, tracks.preferences)
    }
    fun playlistMedia(id: String): LibraryMediaSelection? {
        check(java.awt.EventQueue.isDispatchThread())
        if (closed || presentation.state == LibraryVisualState.scanning) return null
        return LibraryMediaSelection(catalog[id] ?: return null, grant ?: return null, LocalTrackPreferences())
    }
    fun openTracks(kind: String, language: String) {
        if (!closed && presentation.allows(if (kind == "audio") "openLibraryAudio" else "openLibrarySubtitles"))
            tracks = tracks.open(kind, language)
    }
    fun selectTrack(request: syncwatch.design.SelectionMenuRequest) {
        if (closed) return
        tracks = tracks.select(request)
        presentation = presentation.copy(metadataAudioName = tracks.audioName, metadataSubtitleName = tracks.subtitleName,
            metadataSubtitlesDisabled = tracks.subtitlesDisabled)
        stable = presentation
    }
    fun dismissTracks() { tracks = tracks.dismiss() }
    fun clearError() { errorKey = null }
    fun close() {
        closed = true; tracks = tracks.reset(); generation++; metadataGeneration++; pending?.cancel(); metadataTask?.cancel()
        monitorJob?.cancel(); monitor?.close(); monitor = null; monitorAccess = null
        scope.cancel(); grant = null; catalog = emptyMap(); durations.clear()
        presentation = presentation.copy(enabledActions = emptySet())
    }
    suspend fun settled() { pending?.join() }
    suspend fun metadataSettled() { metadataTask?.join() }
    suspend fun closeAndWait() = withContext(kotlinx.coroutines.NonCancellable) {
        close(); scope.coroutineContext[Job]?.join(); metadata?.close()
    }
    private fun cancelMetadata() {
        metadataGeneration++; metadataTask?.cancel(); tracks = tracks.reset()
        presentation = presentation.copy(metadataDurationMs = null, metadataWidth = 0, metadataHeight = 0,
            metadataAudioTracks = null, metadataSubtitleTracks = null, metadataAudioName = null, metadataSubtitleName = null,
            metadataSubtitlesDisabled = false, metadataLoading = false, metadataUnavailable = false)
    }
    private fun publishDuration(value: Long?, file: LocalMediaFile) {
        durations.record(value, file)
        presentation = presentation.copy(entries = presentation.entries.map { row ->
            if (row.id == file.id) row.copy(durationMs = value) else row
        })
    }
    private fun startMetadata() {
        cancelMetadata(); stable = presentation
        val reader = metadata ?: return
        val selection = selectedMedia() ?: return
        val current = metadataGeneration
        val files = listOf(selection.media) + presentation.entries.mapNotNull { catalog[it.id] }.filter { it.id != selection.media.id }
        presentation = presentation.copy(metadataLoading = true); stable = presentation
        metadataTask = scope.launch {
            metadataSerial.withLock {
                try {
                    for (file in files) {
                        coroutineContext.ensureActive()
                        if (closed || current != metadataGeneration) return@launch
                        val selected = file.id == selection.media.id
                        if (!selected && durations.duration(file) != null) continue
                        try {
                            val value = reader.read(file)
                            coroutineContext.ensureActive()
                            if (closed || current != metadataGeneration) return@launch
                            publishDuration(value.durationMs, file)
                            if (selected) {
                                tracks = tracks.reset(value, file.companions)
                                presentation = presentation.copy(metadataDurationMs = value.durationMs, metadataWidth = value.width,
                                    metadataHeight = value.height, metadataAudioTracks = value.audioTracks + file.companions.count { it.kind == syncwatch.library.LibraryCompanionKind.audio },
                                    metadataSubtitleTracks = value.subtitleTracks + file.companions.count { it.kind == syncwatch.library.LibraryCompanionKind.subtitles }, metadataLoading = false,
                                    metadataAudioName = tracks.audioName, metadataSubtitleName = tracks.subtitleName,
                                    metadataSubtitlesDisabled = tracks.subtitlesDisabled,
                                    enabledActions = presentation.enabledActions + setOf("openLibraryAudio", "openLibrarySubtitles"))
                            }
                            stable = presentation
                        } catch (_: CancellationException) { return@launch }
                        catch (_: Exception) {
                            if (closed || current != metadataGeneration) return@launch
                            publishDuration(null, file)
                            if (selected) presentation = presentation.copy(metadataLoading = false, metadataUnavailable = true)
                            stable = presentation
                        }
                    }
                } finally { java.lang.ref.Reference.reachabilityFence(selection.access) }
            }
        }
    }
    private suspend fun <T> storage(operation: () -> T): T = persistence.withLock { withContext(Dispatchers.IO) { operation() } }
    private fun replaceMonitor(source: LibraryChangeMonitor?, access: LibraryFolderAccess, recursive: Boolean) {
        monitorJob?.cancel(); monitor?.close()
        monitor = source; monitorAccess = if (source == null) null else access
        monitorRecursive = if (source == null) null else recursive
        monitorJob = source?.let { owner -> scope.launch {
            try {
                for (event in owner.changes) {
                    delay(300); pending?.join(); coroutineContext.ensureActive()
                    if (closed || monitor !== owner) return@launch
                    start(automatic = true, persistAccess = false) { access }
                    pending?.join()
                    if (event == LibraryMonitorEvent.unavailable || !owner.isActive) {
                        // Reconciliation owns failures even if the UI already dismissed its error.
                        return@launch
                    }
                }
            } catch (_: CancellationException) { }
            catch (_: Exception) { if (!closed && monitor === owner) errorKey = "libraryWatchFailed" }
            finally {
                withContext(NonCancellable) { owner.closeAndWait() }
                java.lang.ref.Reference.reachabilityFence(access)
            }
        } }
    }
    private fun start(automatic: Boolean = false, persistAccess: Boolean = true, acquire: suspend () -> LibraryFolderAccess?) {
        if (closed) return
        pending?.cancel(); if (!automatic) cancelMetadata(); val current = ++generation
        val previous = stable ?: presentation
        stable = previous
        if (!automatic) { presentation = previous.copy(state = LibraryVisualState.scanning); errorKey = null }
        pending = scope.launch {
            var candidate: LibraryChangeMonitor? = null
            try {
                val next = acquire()
                coroutineContext.ensureActive()
                if (next == null) { if (generation == current) { presentation = previous; startMetadata() }; return@launch }
                val recursive = scanRecursively(next); val refresh = refreshAutomatically(next)
                val reuse = refresh && monitor?.isActive == true && monitorAccess?.rootId == next.rootId &&
                    monitorAccess?.root == next.root && monitorRecursive == recursive
                var watchFailed = false
                if (refresh && !reuse) {
                    try {
                        candidate = withContext(Dispatchers.IO + NonCancellable) { LibraryChangeMonitor.open(next.root, recursive) }
                    } catch (_: Exception) { watchFailed = true }
                }
                coroutineContext.ensureActive()
                val files = scanner.scan(next.root, next.rootId, recursive)
                coroutineContext.ensureActive()
                if (closed || current != generation) return@launch
                if (automatic && files.associateBy { it.id } == catalog) {
                    if (!reuse) { replaceMonitor(candidate, next, recursive); candidate = null }
                    if (watchFailed) errorKey = "libraryWatchFailed"
                    return@launch
                }
                if (automatic) cancelMetadata()
                // Companion and folder-presentation changes stay outside room activity.
                val publishRoom = !automatic || !sameRoomCatalog(files)
                fun compare(a: String, b: String) = java.util.Arrays.compareUnsigned(a.toByteArray(Charsets.UTF_8), b.toByteArray(Charsets.UTF_8))
                durations.reconcile(files)
                val rows = files.sortedWith { a, b -> compare(a.title.lowercase(), b.title.lowercase()).takeIf { it != 0 } ?: compare(a.relativePath, b.relativePath) }
                    .map { LibraryEntry(it.id, it.title, it.file.toString(), durations.duration(it), it.folderMovie) }
                val selected = listOfNotNull(if (automatic) presentation.selectedID else previous.selectedID, savedSelection).firstOrNull { id -> rows.any { it.id == id } } ?: rows.firstOrNull()?.id
                grant = next
                catalog = files.associateBy { it.id }
                if (publishRoom) roomRevision = java.util.UUID.randomUUID()
                presentation = LibraryPresentation(if (rows.isEmpty()) LibraryVisualState.empty else LibraryVisualState.selected,
                    rows, selected, setOf("chooseFolder", "rescan", "search", "selectMovie", "openSettings"))
                startMetadata()
                if (!reuse) { replaceMonitor(candidate, next, recursive); candidate = null }
                if (watchFailed) errorKey = "libraryWatchFailed"
                if (!persistAccess) return@launch
                try { storage { store.write(next.record) } }
                catch (_: CancellationException) { }
                catch (_: Exception) { if (current == generation) errorKey = "librarySaveFailed" }
            } catch (_: CancellationException) { }
            catch (_: Exception) { if (!closed && current == generation) {
                if (!automatic) { presentation = previous; startMetadata() }
                errorKey = "libraryUnavailable"
            } }
            finally { withContext(NonCancellable) { candidate?.closeAndWait() } }
        }
    }
}
