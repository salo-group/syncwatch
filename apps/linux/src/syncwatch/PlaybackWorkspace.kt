package syncwatch

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.*
import java.awt.EventQueue
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import syncwatch.design.*
import syncwatch.domain.*
import syncwatch.bridge.*
import syncwatch.playback.VLCPlayback
import syncwatch.playback.X11Display
import syncwatch.playback.X11VideoCanvas

/** Explicit development entry. Normal library and shared-watch activation have separate owners. */
internal data class LocalPlaybackLaunch(val file: Path, val packageDirectory: Path, val bridge: Path,
    val report: Path?, val detachProbe: Boolean) {
    fun initializeDisplay(): X11Display = VLCPlayback.initializeDisplay(bridge, packageDirectory)
    companion object {
        fun read(arguments: Array<String>): LocalPlaybackLaunch? {
            fun value(flag: String): String? = arguments.indexOf(flag).takeIf { it >= 0 }
                ?.let { arguments.getOrNull(it + 1) }
            if ("--local-playback" !in arguments) return null
            fun required(flag: String) = Path.of(requireNotNull(value(flag)) { flag }).toAbsolutePath().normalize()
            val report = value("--playback-report")?.let { Path.of(it).toAbsolutePath().normalize() }
            require(report == null || !java.nio.file.Files.exists(report)) { "playback-report-exists" }
            val media = MediaConfiguration.read(arguments)
            return LocalPlaybackLaunch(required("--local-playback"), media.packageDirectory, media.bridge,
                report, "--playback-detach-probe" in arguments)
        }
    }
}

/** Presentation/controller policy only; native commands stay on the playback worker. */
internal class PlaybackController(
    private val launch: LocalPlaybackLaunch,
    display: X11Display,
    parentScope: CoroutineScope,
    requestedScope: PlaybackScope? = null,
    private val initialPositionMs: Long = 0,
    private var selection: LibraryMediaSelection? = null,
    private var requiresSharedAdmission: Boolean = false,
    private val onLoaded: ((PlaybackScope) -> Unit)? = null,
    private val onObservation: ((PlaybackObservation) -> Unit)? = null,
    private val onNavigation: ((String) -> Unit)? = null,
    private val onFailure: (() -> Unit)? = null,
    private val onPlaylistUpdate: ((WatchPlaylist) -> Unit)? = null,
    private val onPlaylistSelection: ((String) -> Unit)? = null,
    autoplay: Boolean = false,
) {
    private val ownerJob = SupervisorJob(parentScope.coroutineContext[Job])
    private val work = CoroutineScope(parentScope.coroutineContext + ownerJob)
    internal val player = VLCPlayback()
    internal val scope = requestedScope ?: PlaybackScope.fresh().replacingMedia()
    internal val tracks by lazy { LocalPlaybackTracks(player, scope, work, ExternalTrackPicker { canvas }) }
    val playlist = WatchPlaylist()
    var language = "en"
    internal fun libraryPresentation(source: LibraryPresentation) = tracks.libraryPresentation(source)
    internal val loaded = CompletableDeferred<Unit>()
    internal val acceptanceRequested = launch.report != null
    private var autoplayPending = autoplay && !requiresSharedAdmission
    private var started = false
    private var closing = false
    private var shutdown: CompletableFuture<Void>? = null
    private var sharedSession: HelperSession? = null
    private var sharedMediaPreparation = SharedMediaPreparation(work)
    private var sharedAuthority: (() -> Boolean)? = null
    var sharedState by mutableStateOf<HelperSessionState?>(null)
        private set
    private val navigationActions get() = if (onNavigation == null) setOf("endWatching", "toggleFullscreen")
        else setOf("endWatching", "toggleFullscreen", "showLibrary")
    val canvas = X11VideoCanvas(display, ::closeNative)
    var presentation by mutableStateOf(PlayerPresentation(title = launch.file.fileName?.toString().orEmpty(),
        enabledActions = navigationActions))
        private set
    var failed by mutableStateOf(false)
        private set

    suspend fun prepareSharedMedia(): MediaDescriptor {
        check(!closing)
        val descriptor = sharedMediaPreparation.prepare(checkNotNull(selection), player, scope)
        check(!closing)
        sharedResumePositionMs = minOf(initialPositionMs, checkNotNull(descriptor.durationMs))
        return descriptor
    }
    fun requireSharedAdmission() { requiresSharedAdmission = true; gateSharedActions() }
    private var sharedResumePositionMs = 0L
    fun admitSharedPlayback(autoplay: Boolean = false) {
        check(sharedSession != null && sharedState?.connection == HelperConnection.connected && sharedState?.playbackReady == true && sharedAuthority?.invoke() != false)
        // Syncplay may clear readiness after this user's Pause; the admitted file can resume.
        requiresSharedAdmission = false
        if (autoplay) checkNotNull(sharedSession).playFrom(sharedResumePositionMs)
    }
    suspend fun revokeShared(releaseAdmission: Boolean = false) = withContext(NonCancellable) {
        requireSharedAdmission(); sharedAuthority = null
        val session = sharedSession; sharedSession = null; sharedState = null
        val preparation = sharedMediaPreparation
        preparation.close(); session?.close()
        try { player.setPaused(true, scope) } catch (_: Exception) { }
        if (!closing) { sharedMediaPreparation = SharedMediaPreparation(work); requiresSharedAdmission = !releaseAdmission }
    }
    /** The room coordinator admits local media before transferring this transport. */
    suspend fun connectShared(transport: HelperTransport, endpoint: SyncplayEndpoint, media: SharedMedia, revision: String, requireDeclaredIdentity: Boolean = false, authority: (() -> Boolean)? = null) {
        check(EventQueue.isDispatchThread())
        try {
            check(!closing && started && sharedSession == null && authority?.invoke() != false)
            sharedAuthority = authority
            sharedMediaPreparation.validate(player, scope)
            check(!closing && started && sharedSession == null && authority?.invoke() != false)
            val session = HelperSession(transport, player, scope, endpoint, media, revision, work, requiresSharedAdmission || requireDeclaredIdentity) { state ->
                sharedState = state; gateSharedActions()
            }
            sharedSession = session; sharedState = session.state; gateSharedActions(); session.start()
        } catch (error: Exception) { withContext(NonCancellable + Dispatchers.IO) { transport.close() }; throw error }
    }
    private fun gateSharedActions() {
        if ((requiresSharedAdmission || sharedSession != null) &&
            (sharedAuthority?.invoke() == false || sharedState?.connection != HelperConnection.connected || (requiresSharedAdmission && sharedState?.playbackReady != true)))
            presentation = presentation.copy(enabledActions = presentation.enabledActions - setOf("playPause", "seek", "skip"))
    }

    fun start() {
        check(EventQueue.isDispatchThread())
        if (started || closing) return
        started = true
        work.launch {
            try {
                player.open(launch.bridge, launch.packageDirectory, canvas)
                player.load(launch.file, scope)
                var admissionPending = true
                var resumePending = initialPositionMs > 0
                while (isActive && !closing) {
                    val value = player.observe(scope)
                    if (value.status == PlaybackStatus.failed) throw WatchFailure("media")
                    // Restore the local position before track acknowledgement and admission.
                    if (resumePending && value.status == PlaybackStatus.ready) {
                        resumePending = false
                        if (value.seekable) {
                            try { player.seek(minOf(initialPositionMs, value.durationMs ?: initialPositionMs), scope) }
                            catch (cancelled: CancellationException) { throw cancelled }
                            catch (_: Exception) { /* The next observation remains authoritative. */ }
                            continue
                        }
                    }
                    if (admissionPending && value.status == PlaybackStatus.ready) {
                        tracks.applyInitial(selection?.tracks ?: LocalTrackPreferences())
                        ensureActive()
                        if (closing) break
                        admissionPending = false; loaded.complete(Unit); onLoaded?.invoke(scope)
                        continue
                    }
                    apply(value)
                    onObservation?.invoke(value)
                    if (autoplayPending && value.status == PlaybackStatus.ready && !value.ended) {
                        autoplayPending = false
                        player.setPaused(false, scope)
                    }
                    delay(100)
                }
            } catch (_: CancellationException) {
                loaded.completeExceptionally(CancellationException("closed"))
            } catch (error: Exception) {
                loaded.completeExceptionally(error)
                if (!closing) { tracks.dismiss(); failed = true; presentation = presentation.copy(enabledActions = setOf("endWatching")); onFailure?.invoke() }
            }
        }
    }

    private fun apply(value: PlaybackObservation) {
        val state = when {
            value.ended -> PlayerVisualState.ended
            value.buffering -> PlayerVisualState.buffering
            value.status != PlaybackStatus.ready -> PlayerVisualState.loading
            value.paused -> PlayerVisualState.paused
            else -> PlayerVisualState.playing
        }
        val enabled = navigationActions.toMutableSet().apply { add("playPause") }
        if (value.seekable) enabled.addAll(setOf("seek", "skip"))
        if (value.volume != null) enabled.addAll(setOf("setMovieVolume", "muteMovie"))
        if (value.status == PlaybackStatus.ready && !value.ended) enabled.addAll(setOf("selectAudio", "selectSubtitles"))
        else tracks.dismiss()
        presentation = presentation.copy(state = state, positionMs = value.positionMs,
            durationMs = value.durationMs ?: 0, volume = value.volume ?: presentation.volume,
            muted = value.muted, enabledActions = enabled)
        gateSharedActions()
        refreshPlaylist()
    }

    private fun refreshPlaylist() { onPlaylistUpdate?.invoke(playlist); presentation = playlist.presentation(presentation) }
    fun selectMenu(request: SelectionMenuRequest) {
        refreshPlaylist()
        if (request.kind == "playlist") playlist.selection(request)?.let { onPlaylistSelection?.invoke(it) }
        else tracks.select(request)
    }
    fun dismissMenu() { playlist.dismiss(); tracks.dismissMenu() }
    fun action(action: String, value: Double?, close: () -> Unit, fullscreen: () -> Unit) {
        if (closing) return
        gateSharedActions()
        refreshPlaylist()
        presentation.dispatch(action, value) { accepted, number ->
            if (accepted == "openPlaylist") { tracks.dismiss(); playlist.open(); return@dispatch }
            if (accepted in setOf("previousFile", "nextFile")) {
                playlist.neighbor(if (accepted == "previousFile") -1 else 1)?.let { onPlaylistSelection?.invoke(it) }
                return@dispatch
            }
            playlist.dismiss()
            if (accepted in setOf("selectAudio", "selectSubtitles")) {
                tracks.open(if (accepted == "selectAudio") "audio" else "subtitles", language)
                return@dispatch
            }
            if (accepted in setOf("showLibrary", "endWatching", "toggleFullscreen")) tracks.dismiss()
            if (accepted in setOf("endWatching", "showLibrary") && onNavigation != null) {
                onNavigation.invoke(accepted)
                return@dispatch
            }
            val shared = sharedSession
            if (shared != null && accepted in setOf("playPause", "seek")) {
                try {
                    if (accepted == "playPause") shared.togglePause() else if (number != null) shared.seek(number.toLong())
                } catch (_: Exception) { /* A rejected intent cannot acknowledge playback state. */ }
                return@dispatch
            }
            when (accepted) {
                "endWatching" -> requestClose(close)
                "toggleFullscreen" -> fullscreen()
                else -> work.launch {
                    try {
                        when (accepted) {
                            "playPause" -> player.setPaused(presentation.playing, scope)
                            "seek" -> if (number != null) player.seek(number.toLong(), scope)
                            "setMovieVolume" -> if (number != null) player.setVolume(number, presentation.muted, scope)
                            "muteMovie" -> player.setVolume(presentation.volume, !presentation.muted, scope)
                        }
                    } catch (_: Exception) { /* Rejection does not acknowledge a changed observation. */ }
                }
            }
        }
    }

    private fun closeNative(): CompletableFuture<Void> {
        check(EventQueue.isDispatchThread())
        shutdown?.let { return it.copy() }
        closing = true
        playlist.close()
        loaded.completeExceptionally(CancellationException("closed"))
        presentation = presentation.copy(enabledActions = emptySet())
        val result = CompletableFuture<Void>(); shutdown = result
        // The shutdown receipt owns this cleanup job after the view scope ends.
        val cleanupJob = Job()
        val cleanup = CoroutineScope(work.coroutineContext.minusKey(Job) + cleanupJob)
        cleanup.launch(start = CoroutineStart.UNDISPATCHED) {
            var helperFailure: Exception? = null
            try { tracks.close(); sharedMediaPreparation.close(); sharedSession?.close() } catch (error: Exception) { helperFailure = error }
            finally {
                sharedSession = null; ownerJob.cancel()
                player.closeAsync().whenComplete { _, error -> EventQueue.invokeLater {
                    tracks.releaseExternalAccess()
                    if (error == null) {
                        java.lang.ref.Reference.reachabilityFence(selection); selection = null
                    }
                    val failure = error ?: helperFailure
                    if (failure == null) result.complete(null) else result.completeExceptionally(failure)
                    cleanupJob.complete()
                } }
            }
        }
        return result.copy()
    }

    fun requestClose(onClosed: () -> Unit) {
        val release = closeAndRelease()
        release.whenComplete { _, error -> EventQueue.invokeLater {
            if (error == null) onClosed() else failed = true
        } }
    }
    fun closeAndRelease(): CompletableFuture<Void> = canvas.releaseAfter(closeNative())
}

@Composable internal fun PlaybackWorkspace(controller: PlaybackController, onClose: () -> Unit, onFullscreen: () -> Unit) {
    val preferences = remember { AppearancePreferences(FileLocalStateStore(AppearancePreferences.defaultPath())) }
    val language = preferences.state.preferences.language
    val dark = when (preferences.state.preferences.theme) { "dark" -> true; "light" -> false; else -> isSystemInDarkTheme() }
    val theme = if (dark) SyncWatchTheme.dark else SyncWatchTheme.light
    ConfiguredPlaybackPane(controller, preferences, theme, onClose, onFullscreen)
    LaunchedEffect(controller.failed) {
        if (controller.failed && !controller.acceptanceRequested) javax.swing.JOptionPane.showMessageDialog(null,
            SyncWatchCopy.text("playbackFailed", language), "SyncWatch", javax.swing.JOptionPane.ERROR_MESSAGE)
    }
}

@Composable internal fun PlaybackPane(controller: PlaybackController, language: String, theme: SyncWatchTheme,
    onClose: () -> Unit, onFullscreen: () -> Unit, roomNotice: String = "", skipSeconds: Int? = null,
    onOpenSettings: (() -> Unit)? = null) {
    LaunchedEffect(controller) { controller.start() }
    val observed = controller.playlist.presentation(controller.presentation)
    val configured = observed.copy(roomNotice = roomNotice, skipSeconds = skipSeconds ?: observed.skipSeconds,
        enabledActions = observed.enabledActions + if (onOpenSettings != null) setOf("openPlayerSettings") else emptySet())
    PlayerChrome(configured, language, theme,
        videoSurface = { NativePlayerSurface(controller.canvas) },
        selectionMenu = controller.playlist.menu ?: controller.tracks.menu, onSelection = controller::selectMenu,
        onDismissSelection = controller::dismissMenu,
        onAction = { action, value ->
            controller.language = language
            if (action == "openPlayerSettings" && onOpenSettings != null) { controller.dismissMenu(); onOpenSettings() }
            else controller.action(action, value, onClose, onFullscreen)
        })
}
