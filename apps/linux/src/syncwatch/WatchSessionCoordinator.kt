package syncwatch

import androidx.compose.runtime.*
import kotlinx.coroutines.*
import syncwatch.design.LibraryPresentation
import syncwatch.domain.*
import syncwatch.playback.X11Display

/** Execute local resource effects. Shared playback commands remain with the synchronization adapter. */
internal class WatchSessionCoordinator(
    private val preferences: AppearancePreferences,
    private val media: MediaConfiguration,
    private val display: X11Display?,
    parent: CoroutineScope,
) {
    private val ownerJob = SupervisorJob(parent.coroutineContext[Job])
    private val work = CoroutineScope(parent.coroutineContext + ownerJob)
    private val session = WatchSession(preferences.state)
    var player by mutableStateOf<PlaybackController?>(null)
        private set
    var playerPresented by mutableStateOf(false)
        private set
    var runtimeAvailable by mutableStateOf(display != null)
        private set
    var phase by mutableStateOf(session.phase)
        private set
    var errorKey by mutableStateOf<String?>(null)
        private set
    val scope get() = session.scope
    val positionMs get() = session.positionMs
    var onEndWatching: (() -> Unit)? = null
    var onPlaylistUpdate: ((WatchPlaylist) -> Unit)? = null
    var onPlaylistSelection: ((String) -> Unit)? = null
    val mediaId get() = session.mediaId
    private var pendingLoad: Job? = null
    private var pendingScope: PlaybackScope? = null
    private var releaseJob: Deferred<Unit>? = null
    private var releasingScope: PlaybackScope? = null
    private var checkpointJob: Job? = null
    private var checkpointRequested = false
    private var hasWatchActivity = false
    private var closing = false
    private var shutdown: Deferred<Unit>? = null

    fun prepareRuntime() { if (!closing && !runtimeAvailable) errorKey = "playbackFailed" }
    fun libraryPresentation(source: LibraryPresentation): LibraryPresentation {
        if (closing) return source.copy(enabledActions = emptySet())
        val enabled = source.enabledActions.toMutableSet()
        val live = source.selectedID == session.mediaId && player?.scope == session.scope &&
            phase in setOf(WatchPhase.active, WatchPhase.preparing) && player?.failed == false
        if (runtimeAvailable && !preferences.savingWatchState && !live) enabled += "startWatching"
        if (live) { enabled += setOf("continueWatching", "endWatching"); enabled -= setOf("openLibraryAudio", "openLibrarySubtitles") }
        if (preferences.savingWatchState) enabled -= "openSettings"
        val result = source.copy(enabledActions = enabled)
        return if (live) player!!.libraryPresentation(result) else result
    }
    fun start(selection: LibraryMediaSelection, requiresSharedAdmission: Boolean = false, autoplay: Boolean = false) {
        check(java.awt.EventQueue.isDispatchThread())
        if (!runtimeAvailable || closing) return
        try {
            session.select(selection.media.id).forEach { if (it is WatchEffect.Release) release(it.scope) }
            val effects = session.start()
            hasWatchActivity = true; errorKey = null
            for (effect in effects) when (effect) {
                is WatchEffect.Release -> release(effect.scope)
                is WatchEffect.Load -> load(effect, selection, requiresSharedAdmission, autoplay)
            }
            publish(); requestCheckpoint()
        } catch (_: WatchFailure) { errorKey = "playbackFailed"; publish() }
    }
    fun continueWatching() {
        if (closing || player?.scope != session.scope || player?.failed != false) return
        try { session.continueWatching(); publish() } catch (_: WatchFailure) { errorKey = "playbackFailed" }
    }
    fun showLibrary() {
        if (closing) return
        session.returnToLibrary(); publish(); requestCheckpoint()
    }
    fun end() {
        pendingLoad?.cancel()
        try { session.end().forEach { if (it is WatchEffect.Release) release(it.scope) } }
        catch (_: WatchFailure) { errorKey = "playbackFailed"; closing = true; release(session.scope) }
        publish(); requestCheckpoint()
    }
    fun preferencesChanged() { session.setPreferences(preferences.state.preferences); publish() }
    fun clearError() { errorKey = null }
    suspend fun close() {
        shutdown?.let { it.await(); return }
        val receipt = work.async(start = CoroutineStart.LAZY) {
            closing = true; end()
            val loading = pendingLoad
            releaseJob?.await(); loading?.join()
            player?.let { release(it.scope); releaseJob?.await() }
            player = null
            checkpointJob?.join()
            Unit
        }
        shutdown = receipt
        receipt.invokeOnCompletion { if (it == null) ownerJob.cancel() }
        receipt.start()
        try { receipt.await() }
        catch (error: Exception) { errorKey = "playbackFailed"; throw error }
    }
    private fun publish() { phase = session.phase; playerPresented = !closing && session.playerPresented }
    private fun load(request: WatchEffect.Load, selection: LibraryMediaSelection, requiresSharedAdmission: Boolean, autoplay: Boolean) {
        pendingLoad?.cancel(); pendingScope = request.scope
        val prior = releaseJob
        val loading = work.launch(start = CoroutineStart.LAZY) {
            try {
                prior?.await()
                ensureActive()
                if (closing || session.scope != request.scope || session.phase != WatchPhase.preparing) return@launch
                player = PlaybackController(LocalPlaybackLaunch(selection.media.file, media.packageDirectory, media.bridge, null, false),
                    requireNotNull(display), work, requestedScope = request.scope, initialPositionMs = request.positionMs,
                    selection = selection, requiresSharedAdmission = requiresSharedAdmission, onLoaded = ::loaded, onObservation = ::observed,
                    onNavigation = { if (it == "showLibrary") showLibrary() else if (it == "endWatching") { onEndWatching?.invoke() ?: end() } },
                    onFailure = { failed(request.scope) }, onPlaylistUpdate = { onPlaylistUpdate?.invoke(it) },
                    onPlaylistSelection = { onPlaylistSelection?.invoke(it) }, autoplay = autoplay,
                    initialVolume = preferences.openingMovieVolume, onVolumeChanged = ::volumeChanged)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { failed(request.scope) }
            finally { if (pendingScope == request.scope) { pendingScope = null; pendingLoad = null } }
        }
        pendingLoad = loading; loading.start()
    }
    private fun release(owner: PlaybackScope) {
        if (pendingScope == owner) pendingLoad?.cancel()
        val current = player?.takeIf { it.scope == owner } ?: return
        if (releasingScope == owner) return
        releasingScope = owner
        val receipt = work.async(start = CoroutineStart.LAZY) {
            val stopped = CompletableDeferred<Unit>()
            current.closeAndRelease().whenComplete { _, error ->
                if (error == null) stopped.complete(Unit) else stopped.completeExceptionally(error)
            }
            stopped.await()
            if (player === current) player = null
            if (releasingScope == owner) releasingScope = null
        }
        releaseJob = receipt; receipt.start()
    }
    private fun loaded(owner: PlaybackScope) {
        if (closing || owner != session.scope || phase != WatchPhase.preparing) return
        try { session.loaded(owner); publish() } catch (_: WatchFailure) { failed(owner) }
    }
    private fun volumeChanged(owner: PlaybackScope, value: Double) {
        if (closing || owner != session.scope || session.phase != WatchPhase.active || !preferences.rememberMovieVolume(value)) return
        preferencesChanged(); requestCheckpoint()
    }
    private fun observed(value: PlaybackObservation) {
        if (closing || value.scope != session.scope || phase != WatchPhase.active || value.status != PlaybackStatus.ready) return
        try { session.recordPlayback(value.scope, value.positionMs, value.durationMs, value.paused, value.ended) }
        catch (_: WatchFailure) { failed(value.scope) }
    }
    private fun failed(owner: PlaybackScope) {
        if (closing || owner != session.scope) return
        errorKey = "playbackFailed"; end()
    }
    private fun requestCheckpoint() {
        if (!hasWatchActivity) return
        checkpointRequested = true
        if (checkpointJob != null) return
        val writing = work.launch(start = CoroutineStart.LAZY) {
            try {
                while (checkpointRequested) {
                    checkpointRequested = false
                    if (!preferences.persistWatchState(session.localState) && errorKey == null) errorKey = "saveFailed"
                }
            } finally { checkpointJob = null }
        }
        checkpointJob = writing; writing.start()
    }
}
