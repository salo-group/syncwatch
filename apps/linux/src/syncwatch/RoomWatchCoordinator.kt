package syncwatch

import androidx.compose.runtime.*
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import syncwatch.bridge.*
import syncwatch.design.*
import syncwatch.library.LocalMediaFile
import syncwatch.domain.*
import syncwatch.room.*

internal class SharedMediaUnavailable : Exception()

/** Metadata admission and helper lifetime; native playback remains owned by the watch coordinator. */
internal class RoomWatchCoordinator(private val room: RoomController, private val watch: WatchSessionCoordinator,
    private val helper: HelperConfiguration, private val scope: CoroutineScope, private val library: LibraryController? = null) {
    private data class ActiveWatch(val membership: RoomWatchMembership, val context: RoomWatchContext, val player: PlaybackController)
    private data class NextWatch(val selection: LibraryMediaSelection, val forceSelection: Boolean)
    private var pendingSelection: LibraryMediaSelection? = null
    private var active: ActiveWatch? = null
    private var work: Job? = null
    private var seenMembership = false
    private var closing = false
    private var admitted = false
    private var completed by mutableStateOf(false)
    var busy by mutableStateOf(false)
        private set
    var status by mutableStateOf("")
        private set
    var errorKey by mutableStateOf<String?>(null)
        private set
    fun clearError() { errorKey = null }
    fun libraryPresentation(source: LibraryPresentation): LibraryPresentation {
        val result = room.libraryPresentation(source, !busy && active == null)
        val enabled = result.enabledActions.toMutableSet()
        if (room.connected && !busy && active == null && watch.runtimeAvailable && source.selectedID != null) enabled += "startWatching"
        if (busy || closing) enabled -= "startWatching"
        return result.copy(enabledActions = enabled, roomStatus = if (!busy && room.mediaNotice.isNotEmpty()) room.mediaNotice else status.ifEmpty { result.roomStatus })
    }
    val playerNotice: String get() = if (busy) "" else room.mediaNotice.ifEmpty {
        if (active?.player?.sharedState?.connection == HelperConnection.reconnecting) "reconnecting" else ""
    }
    fun membershipChanged() {
        if (!room.connected) { seenMembership = false; return }
        if (seenMembership) return
        seenMembership = true
        if (work == null) watch.player?.let { player ->
            player.requireSharedAdmission(); busy = true
            val task = scope.launch(start = CoroutineStart.LAZY) {
                try { player.revokeShared() }
                finally { busy = false; work = null }
            }
            work = task; task.start()
        }
    }
    fun start(selection: LibraryMediaSelection) {
        check(java.awt.EventQueue.isDispatchThread())
        if (closing || work != null) return
        if (room.editor.state == "disconnected") { watch.start(selection, autoplay = true); return }
        if (!room.connected || !watch.runtimeAvailable) return
        busy = true; errorKey = null; status = "sharedWatchPreparing"
        val task = scope.launch(start = CoroutineStart.LAZY) { run(selection) }
        work = task; task.start()
    }
    private suspend fun run(initial: LibraryMediaSelection, forceSelection: Boolean = false) {
        try {
            var next: NextWatch? = NextWatch(initial, forceSelection)
            while (!closing && currentCoroutineContext().isActive) {
                val current = next ?: break
                next = runOne(current.selection, current.forceSelection)
            }
        } finally { busy = false; work = null }
    }
    private suspend fun runOne(selection: LibraryMediaSelection, forceSelection: Boolean): NextWatch? {
        var publication: RoomPreparedPublication? = null
        var membership: RoomWatchMembership? = null
        var owned: PlaybackController? = null
        var finished = false; var follow = false; var startedGeneration = false; admitted = false; completed = false
        var stage = "membership"
        try {
            val (ticket, intent) = room.beginWatch(); membership = ticket
            stage = "runtime"
            val runtime = helper.verify()
            currentCoroutineContext().ensureActive()
            stage = "input"
            watch.end(); watch.start(selection, requiresSharedAdmission = true)
            val nativeScope = watch.scope
            val deadline = System.nanoTime() + 20_000_000_000L
            while (owned == null) {
                currentCoroutineContext().ensureActive()
                check(room.isCurrent(ticket) && watch.scope == nativeScope && watch.errorKey == null && System.nanoTime() < deadline) { "media-unavailable" }
                // The native input can be ready while the application is still acknowledging its tracks.
                watch.player?.takeIf { watch.phase == WatchPhase.active && it.scope == nativeScope && !it.failed }?.let { player ->
                    try {
                        val actual = player.player.observe(nativeScope)
                        if (actual.status == PlaybackStatus.ready && actual.seekable && (actual.durationMs ?: 0) > 0) owned = player
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) { /* Loading is bounded by the native owner and the admission deadline. */ }
                }
                if (owned == null) delay(100)
            }
            val player = checkNotNull(owned)
            stage = "media"
            val local = player.prepareSharedMedia()
            stage = "publication"
            val published = room.publishPrepared(local, ticket); publication = published
            stage = "generation"
            var context = room.watchContext(ticket)
            requireSameGeneration(intent, context)
            if (forceSelection || context.bootstrap.snapshot.watchStatus != "active") {
                stage = "selection"
                context = room.performWatch(RoomCommandAction.Select(published.media.mediaId), ticket, context)
                check(context.definition.selection?.media == published.media) { "selection-changed" }
                stage = "start"
                context = room.performWatch(RoomCommandAction.Start(published.media.mediaId), ticket, context)
                startedGeneration = true
            }
            requireAvailable(local, ticket, context)
            stage = "agreement"
            val current = room.watchContext(ticket)
            requireSameGeneration(context, current); requireAvailable(local, ticket, current)
            check(watch.player === player && watch.scope == nativeScope) { "media-changed" }
            active = ActiveWatch(ticket, current, player)
            currentCoroutineContext().ensureActive()
            stage = "helper"
            var transport: HelperTransport? = null
            try {
                // Keep ownership even if cancellation wins the dispatch back to the UI thread.
                withContext(Dispatchers.IO) { transport = HelperProcess.start(runtime.executable, runtime.packageDirectory) }
                currentCoroutineContext().ensureActive(); check(authorized()) { "watch-revoked" }
                val connection = current.bootstrap.connection; val remote = checkNotNull(current.definition.selection).media
                player.connectShared(checkNotNull(transport), SyncplayEndpoint(connection.host, connection.port, connection.room, ticket.identity.syncplayUsername),
                    SharedMedia("loaded-" + UUID.randomUUID(), remote.title, checkNotNull(local.durationMs), local.fileSizeBytes),
                    runtime.revision, requireDeclaredIdentity = true, authority = ::authorized,
                    admissionProvider = if (room.accountAuthority == null) null else suspend {
                        check(canAdmitTransport()) { "watch-revoked" }
                        val fresh = room.watchContext(ticket)
                        requireSameGeneration(current, fresh); requireMatch(local, fresh)
                        val admission = room.syncTicket(ticket)
                        check(canAdmitTransport()) { "watch-revoked" }
                        SyncplayAdmission(admission.admissionCredential(), ticket.identity.clientRunId)
                    })
                transport = null
            } finally { withContext(NonCancellable + Dispatchers.IO) { transport?.close() } }
            val readyDeadline = System.nanoTime() + 20_000_000_000L
            while (true) {
                currentCoroutineContext().ensureActive()
                if (pendingSelection != null) break
                val latest = room.watchBootstrap
                if (admitted && room.connected && latest != null && latest.snapshot.epoch == current.bootstrap.snapshot.epoch &&
                    latest.snapshot.watchGeneration > current.bootstrap.snapshot.watchGeneration && latest.snapshot.watchStatus in setOf("selected", "active")) {
                    player.requireSharedAdmission(); follow = true; break
                }
                val actual = player.player.observe(nativeScope)
                if (completed || actual.ended) {
                    if (!admitted) { finished = true; status = "ended"; break }
                    check(authorized()) { "watch-revoked" }
                    if (!completed) {
                        busy = true
                        player.revokeShared()
                        publication?.let { room.clearPrepared(it.lease, ticket) }; publication = null
                        if (room.editor.owner && room.isCurrent(ticket)) {
                            try { room.performWatch(RoomCommandAction.End(RoomWatchEndReason.finished), ticket, current) }
                            catch (cancelled: CancellationException) { throw cancelled }
                            catch (_: Exception) { /* Completion metadata cannot control a peer's movie. */ }
                        }
                        currentCoroutineContext().ensureActive()
                        completed = true; busy = false; status = "ended"
                    }
                    // Keep only generation observation; the completed helper is already joined.
                    delay(100)
                    continue
                }
                check(authorized() && !player.failed && player.sharedState?.connection !in setOf(HelperConnection.failed, HelperConnection.stopped)) { "watch-revoked" }
                if (player.sharedState?.connection == HelperConnection.connected && player.sharedState?.playbackReady == true) {
                    check(player.sharedState?.acceptedUsername == ticket.identity.syncplayUsername) { "identity-changed" }
                    if (!admitted) {
                        if (ticket.memberId in current.bootstrap.missingMembers) {
                            val recovered = room.performWatch(RoomCommandAction.Missing(current.definition.selection.media.mediaId, false), ticket, current)
                            requireSameGeneration(current, recovered)
                        }
                        player.admitSharedPlayback(autoplay = startedGeneration); admitted = true; stage = "playback"
                    }
                    busy = false; status = "sharedWatchConnected"
                } else if (player.sharedState?.connection == HelperConnection.reconnecting) status = "reconnecting"
                else check(admitted || System.nanoTime() < readyDeadline) { "helper-timeout" }
                delay(100)
            }
        } catch (_: CancellationException) {
        } catch (error: RoomProtocolException) {
            if (error.code == "staleGeneration") follow = true
            else { errorKey = failureKey(error, owned?.sharedState?.identityRejected == true); status = checkNotNull(errorKey) }
        } catch (error: Exception) {
            val key = failureKey(error, owned?.sharedState?.identityRejected == true)
            // Fixed vocabulary only; exception text can contain private local or room data.
            System.err.println("syncwatch.shared-watch failure stage=$stage key=$key")
            errorKey = key; status = key
        }
        finally {
            withContext(NonCancellable) {
                val previous = active; val completedAdmission = admitted; active = null; admitted = false; completed = false
                try {
                    owned?.revokeShared()
                    if (!completedAdmission && owned != null && watch.player === owned) watch.end()
                    if (finished && previous != null && room.editor.owner && room.isCurrent(previous.membership)) {
                        try { room.performWatch(RoomCommandAction.End(RoomWatchEndReason.finished), previous.membership, previous.context) }
                        catch (_: Exception) { /* Completion metadata is not a peer playback command. */ }
                    }
                }
                finally {
                    publication?.let { value -> membership?.let { room.clearPrepared(value.lease, it) } }
                }
            }
        }
        if (closing || !currentCoroutineContext().isActive) return null
        pendingSelection?.let { requested ->
            pendingSelection = null; busy = true; status = "sharedWatchPreparing"
            return NextWatch(requested, true)
        }
        if (follow && membership != null) {
            try {
                busy = true; status = "sharedWatchPreparing"
                val deadline = System.nanoTime() + 20_000_000_000L
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val current = room.watchContext(membership)
                    val selected = current.definition.selection
                    if (current.bootstrap.snapshot.watchStatus == "active" && selected != null) {
                        val candidate = resolvePlaylist(selected.media)
                        if (candidate == null) {
                            room.performWatch(RoomCommandAction.Missing(selected.media.mediaId, true), membership, current)
                            throw SharedMediaUnavailable()
                        }
                        library?.select(candidate.media.id)
                        return NextWatch(candidate, false)
                    }
                    check(current.bootstrap.snapshot.watchStatus == "selected" && System.nanoTime() < deadline) { "playlist-transition-timeout" }
                    delay(100)
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { watch.end(); errorKey = failureKey(error, false); status = checkNotNull(errorKey) }
        }
        return null
    }
    private fun authorized(): Boolean {
        val value = active ?: return false
        if (closing || pendingSelection != null || watch.player !== value.player || watch.scope != value.player.scope) return false
        // Metadata generations govern future admission; Syncplay owns the admitted native scope.
        if (admitted) return room.canContinuePlayback(value.membership)
        val current = room.watchBootstrap ?: return false
        return room.isCurrent(value.membership) &&
            current.snapshot.epoch == value.context.bootstrap.snapshot.epoch && current.snapshot.watchGeneration == value.context.bootstrap.snapshot.watchGeneration && current.snapshot.watchStatus == "active"
    }
    private suspend fun requireAvailable(local: MediaDescriptor, ticket: RoomWatchMembership, context: RoomWatchContext) {
        try { requireMatch(local, context) }
        catch (error: SharedMediaUnavailable) {
            if (ticket.memberId !in context.bootstrap.missingMembers) {
                room.performWatch(RoomCommandAction.Missing(checkNotNull(context.definition.selection).media.mediaId, true), ticket, context)
            }
            throw error
        }
    }
    private suspend fun stop() { pendingSelection = null; work?.cancelAndJoin() }
    private fun key(file: LocalMediaFile) = MediaDescriptor.normalizedTitleKey(MediaDescriptor.logicalTitle(file.title)) to file.sizeBytes
    private val sharedPlaylistAvailable: Boolean get() {
        val value = active ?: return false
        if (!admitted || !room.connected || watch.player !== value.player || watch.scope != value.player.scope) return false
        if (!completed) return value.player.sharedState?.connection == HelperConnection.connected
        val latest = room.watchBootstrap ?: return false
        return room.isCurrent(value.membership) && completedPlaylistAvailable(value.context.bootstrap.snapshot, latest.snapshot)
    }
    fun updatePlaylist(playlist: WatchPlaylist) {
        val library = library
        if (library == null || closing) { playlist.update(emptyList(), null, false); return }
        val files = library.roomFiles.associateBy { it.id }
        val local = room.editor.state == "disconnected"
        val enabled = watch.phase == WatchPhase.active && !busy && (local || sharedPlaylistAvailable)
        val view = room.catalogView
        val members = view?.bootstrap?.snapshot?.members?.map { it.memberId }.orEmpty()
        val keys = view?.items?.groupBy { it.memberId }?.mapValues { (_, items) -> items.map { it.media.titleKey to it.media.fileSizeBytes }.toSet() }.orEmpty()
        val entries = library.presentation.entries.filter { it.id in files }.map { SelectionMenuEntry(it.id, it.title) }
        val neighbors = files.values.filter { file -> !local && members.size > 1 && members.all { key(file) in keys[it].orEmpty() } }.map { it.id }.toSet()
        playlist.update(entries, watch.mediaId, enabled, neighbors)
    }
    fun selectPlaylist(id: String) {
        if (closing || busy || watch.mediaId == id) return
        val library = library ?: return
        val selection = library.playlistMedia(id) ?: return
        val check = WatchPlaylist(); updatePlaylist(check); check.open()
        if (check.menu?.allows(id) != true) return
        if (room.editor.state == "disconnected") { library.select(id); watch.start(selection, autoplay = true) }
        else if (sharedPlaylistAvailable) {
            pendingSelection = selection; busy = true; status = "sharedWatchPreparing"; active?.player?.requireSharedAdmission()
        }
    }
    private fun resolvePlaylist(media: MediaDescriptor): LibraryMediaSelection? {
        val library = library ?: return null
        val candidates = library.roomFiles.filter { key(it) == (media.titleKey to media.fileSizeBytes) }.take(2)
        return candidates.singleOrNull()?.let { library.playlistMedia(it.id) }
    }
    suspend fun end() {
        val previous = active; stop()
        if (previous != null && room.editor.owner && room.isCurrent(previous.membership)) {
            try { room.performWatch(RoomCommandAction.End(RoomWatchEndReason.user), previous.membership, previous.context) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* Local End already released its own playback. */ }
        }
        watch.end(); status = ""
    }
    private var accountBinding = 0L
    private val accountBindingLock = Mutex()
    suspend fun bindAccount(authority: RoomAccountAuthority?, name: String) {
        if (closing) return
        val revision = ++accountBinding
        // Compose may cancel the caller when its account key changes. Finish owned revocation first.
        withContext(NonCancellable) {
            accountBindingLock.withLock {
                if (closing || accountBinding != revision) return@withLock
                if (room.accountAuthority !== authority && room.hasRoomScope) leave()
                if (!closing && accountBinding == revision) room.bindAccount(authority, name)
            }
        }
    }
    suspend fun leave() {
        stop(); room.close(); watch.player?.revokeShared(releaseAdmission = true)
        status = ""; seenMembership = false
    }
    suspend fun close() = withContext(NonCancellable) {
        closing = true; accountBinding++
        accountBindingLock.withLock { stop() }
    }
    private fun canAdmitTransport(): Boolean {
        val current = active ?: return false; val latest = room.watchBootstrap ?: return false
        return authorized() && room.isCurrent(current.membership) && latest.snapshot.epoch == current.context.bootstrap.snapshot.epoch &&
            latest.snapshot.watchGeneration == current.context.bootstrap.snapshot.watchGeneration && latest.snapshot.watchStatus == "active" &&
            latest.snapshot.mediaId == current.context.bootstrap.snapshot.mediaId
    }
    companion object {
        fun completedPlaylistAvailable(expected: RoomSnapshot, latest: RoomSnapshot): Boolean {
            if (expected.epoch != latest.epoch || expected.watchStatus != "active" || expected.mediaId == null || expected.mediaId != latest.mediaId) return false
            if (latest.watchGeneration == expected.watchGeneration) return latest.watchStatus == "active"
            // watch.ended advances the same movie exactly once; later loads are separate authority.
            return latest.watchStatus == "ended" && latest.watchGeneration > expected.watchGeneration && latest.watchGeneration - expected.watchGeneration == 1L
        }
        fun failureKey(error: Exception, identityRejected: Boolean): String = when {
            identityRejected -> "sharedWatchIdentityRejected"
            error is RoomProtocolException && error.code == "staleGeneration" -> "sharedWatchRoomChanged"
            error is SharedMediaUnavailable -> "sharedWatchMediaMissing"
            else -> "sharedWatchFailed"
        }
        fun requireSameGeneration(expected: RoomWatchContext, current: RoomWatchContext) {
            if (expected.bootstrap.snapshot.epoch != current.bootstrap.snapshot.epoch || expected.bootstrap.snapshot.watchGeneration != current.bootstrap.snapshot.watchGeneration) throw RoomProtocolException("staleGeneration")
        }
        fun requireMatch(local: MediaDescriptor, context: RoomWatchContext) {
            val selected = context.definition.selection
            check(context.bootstrap.snapshot.watchStatus == "active" && selected != null) { "watch-inactive" }
            if (MediaAgreement.resolve(selected.media, listOf(local)).status != MediaAgreementStatus.matched) throw SharedMediaUnavailable()
        }
    }
}
