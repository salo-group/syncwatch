package syncwatch

import androidx.compose.runtime.*
import kotlinx.coroutines.*
import syncwatch.design.RoomEditor
import syncwatch.room.*
import syncwatch.library.LocalMediaFile
import syncwatch.domain.MediaDescriptor

internal data class RoomWatchMembership(val owner: RoomMembershipSession, val memberId: String, val identity: RoomIdentity, val epoch: String)
internal data class RoomInvitationTransfer(val owner: RoomMembershipSession, val invitation: RoomInvitation, val revision: java.util.UUID)
internal data class RoomPreparedPublication(val lease: java.util.UUID, val media: MediaDescriptor)

/** The workspace owns membership independently of the visible dialog. */
internal class RoomController(private val scope: CoroutineScope, legacyValidation: Boolean = false, private val accountSource: (() -> RoomAccountAuthority?)? = null) {
    var editor by mutableStateOf(RoomEditor(flow = if (legacyValidation) "legacy" else syncwatch.design.SyncWatchRoomForm.defaultFlow))
    var accountAuthority: RoomAccountAuthority? = null
        private set
    fun bindAccount(authority: RoomAccountAuthority?, name: String) {
        verifyOwner()
        if (!editor.requiresAccount) return
        accountAuthority = authority
        editor = editor.copy(accountReady = authority != null, accountName = if (authority == null) "" else name,
            accountService = authority?.address?.origin?.toString().orEmpty())
    }
    var catalogView by mutableStateOf<RoomCatalogView?>(null)
        private set
    val bootstrap: RoomBootstrap? get() = catalogView?.bootstrap
    var watchBootstrap: RoomBootstrap? = null
        private set
    private val currentAccount: Boolean get() = !editor.requiresAccount || accountAuthority != null && (accountSource == null || accountSource.invoke() === accountAuthority)
    val hasRoomScope: Boolean get() = editor.state != "disconnected" || work != null || playbackSession != null
    val connected: Boolean get() = editor.connected && bootstrap != null && currentAccount
    private var approvals: RoomApprovalController? = null
    private var admission: RoomAdmission? = null
    private var work: Job? = null
    private var session: RoomMembershipSession? = null
    private var playbackSession: RoomMembershipSession? = null
    private var playbackEpoch: String? = null
    private var catalogMap = RoomLocalCatalog()
    private var localRevision: java.util.UUID? = null
    private var acknowledgedRevision: java.util.UUID? = null
    private var localFiles: List<LocalMediaFile> = emptyList()
    private var publisher: Job? = null
    private var missingReport: Job? = null
    private var prepared: Pair<java.util.UUID, MediaDescriptor>? = null
    private var acknowledgedPrepared: RoomPreparedPublication? = null
    private var stopping = false
    private var closing: CompletableDeferred<Unit>? = null
    private fun verifyOwner() { check(java.awt.EventQueue.isDispatchThread()) { "room-event-thread" } }
    fun connect() {
        verifyOwner()
        if (stopping || editor.busy || connected || work != null || !editor.isValid) return
        if (!currentAccount) return
        playbackSession = null; playbackEpoch = null
        val authority = accountAuthority
        val values = editor.values; val owner = editor.mode == "createRoom"
        editor = editor.copy(state = "connecting", status = "")
        val job = scope.launch(start = CoroutineStart.LAZY) { run(values, owner, authority) }; work = job; job.start()
    }
    fun libraryPresentation(source: syncwatch.design.LibraryPresentation, sharedStartAvailable: Boolean = false) = source.copy(
        enabledActions = (source.enabledActions + if (stopping) emptySet() else setOf("openRoom")) -
            if (editor.state != "disconnected" && !(connected && sharedStartAvailable)) setOf("startWatching") else emptySet(),
        roomStatus = if (connected) (editor.status.takeIf { it in setOf("roomCatalogPublishing", "roomCatalogPublishFailed") } ?: "roomMetadataConnected") else if (editor.state == "reconnecting") "reconnecting" else if (editor.busy) "roomConnecting" else if (editor.state == "failed") "roomConnectionFailed" else "disconnected")
    val mediaNotice: String get() {
        if (editor.state == "reconnecting") return "reconnecting"
        if (editor.state == "failed") return if (playbackSession == null) "roomConnectionFailed" else "roomUpdatesUnavailable"
        val value = bootstrap ?: return ""
        val member = admission?.memberId ?: return ""
        if (!connected || value.snapshot.watchStatus != "active") return ""
        if (member in value.missingMembers || localFiles.isEmpty() && prepared == null) return "sharedWatchMediaMissing"
        return if (value.missingMembers.isEmpty()) "" else "roomPeersMissingMedia"
    }
    private fun startMissingReport() {
        val value = bootstrap ?: return
        val mediaId = value.snapshot.mediaId ?: return
        val member = admission?.memberId ?: return
        if (stopping || !connected || missingReport != null || localFiles.isNotEmpty() || prepared != null ||
            value.snapshot.watchStatus != "active" || member in value.missingMembers) return
        val revision = localRevision
        val task = scope.launch(start = CoroutineStart.LAZY) {
            try {
                val (ticket, context) = beginWatch()
                currentCoroutineContext().ensureActive()
                if (stopping || localFiles.isNotEmpty() || prepared != null || localRevision != revision ||
                    context.bootstrap.snapshot.epoch != value.snapshot.epoch ||
                    context.bootstrap.snapshot.watchGeneration != value.snapshot.watchGeneration ||
                    context.bootstrap.snapshot.watchStatus != "active" || context.bootstrap.snapshot.mediaId != mediaId) return@launch
                performWatch(RoomCommandAction.Missing(mediaId, true), ticket, context)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* A later membership/catalog update retries under its current generation. */ }
            finally {
                missingReport = null
                if (localRevision != revision || bootstrap?.snapshot?.epoch != value.snapshot.epoch ||
                    bootstrap?.snapshot?.watchGeneration != value.snapshot.watchGeneration) startMissingReport()
            }
        }
        missingReport = task; task.start()
    }
    fun pasteInvitation(text: String) {
        verifyOwner()
        if (stopping || editor.busy || editor.connected || editor.mode != "joinRoom") return
        editor = try {
            val invitation = RoomInvitation.parse(text)
            editor.edit("roomID", invitation.roomId).edit("invitation", invitation.credential.copyInvitation())
        } catch (_: RoomProtocolException) {
            editor.clearSecrets().edit("roomID", "").copy(status = "roomInvitationInvalid")
        }
    }
    fun decideAdmission(id: String, approve: Boolean) {
        verifyOwner()
        if (!stopping && connected && editor.owner) approvals?.decide(id, approve)
    }
    private var invitationCopyBusy = false
    private var invitationCopyRevision = java.util.UUID.randomUUID()
    suspend fun invitationForCopy(): RoomInvitationTransfer? {
        if (invitationCopyBusy || stopping || !connected || !editor.owner) return null
        val owner = session ?: return null; val value = admission ?: return null
        invitationCopyBusy = true; val revision = java.util.UUID.randomUUID(); invitationCopyRevision = revision
        return try {
            val invitation = if (accountAuthority != null) owner.invitation() else RoomInvitation(value.roomId, value.invitation ?: throw RoomProtocolException("malformed"))
            RoomInvitationTransfer(owner, invitation, revision).takeIf(::canCopy)
        } catch (_: Exception) { currentCoroutineContext().ensureActive(); if (connected && session === owner) editor = editor.copy(status = "roomInvitationCopyFailed"); null }
        finally { invitationCopyBusy = false }
    }
    fun canCopy(transfer: RoomInvitationTransfer): Boolean = !stopping && connected && editor.owner && session === transfer.owner && invitationCopyRevision == transfer.revision
    fun invitationCopyCompleted(copied: Boolean, transfer: RoomInvitationTransfer) { if (canCopy(transfer)) invitationCopyCompleted(copied) }
    private fun startApprovals(owner: RoomMembershipSession) {
        if (approvals != null || !connected || !editor.owner || accountAuthority == null) return
        val controller = RoomApprovalController(owner, scope) { value ->
            if (!stopping && connected && session === owner) editor = editor.copy(pendingRequests = value.rows,
                pendingAvailable = value.available, deciding = value.deciding, pendingStatus = value.notice)
        }
        approvals = controller; controller.start()
    }
    fun invitationToCopy(): String? = if (connected) admission?.let { value -> value.invitation?.let { RoomInvitation(value.roomId, it).copyText() } } else null
    fun invitationCopyCompleted(copied: Boolean) {
        verifyOwner()
        if (connected) editor = editor.copy(status = if (copied) "roomInvitationCopied" else "roomInvitationCopyFailed")
    }
    suspend fun close() = withContext(NonCancellable) {
        verifyOwner()
        closing?.let { it.await(); return@withContext }
        val completion = CompletableDeferred<Unit>(); closing = completion
        stopping = true; playbackSession = null; playbackEpoch = null
        work?.cancelAndJoin()
        work = null; admission = null; catalogView = null
        editor = editor.clearSecrets().copy(state = "disconnected", status = "", roomId = "", memberCount = 0, owner = false)
        stopping = false; completion.complete(Unit); closing = null
    }
    fun updateLibrary(revision: java.util.UUID, files: List<LocalMediaFile>) {
        verifyOwner()
        if (localRevision == revision) return
        localRevision = revision; localFiles = files.toList(); startPublisher(); startMissingReport()
    }
    fun isCurrent(membership: RoomWatchMembership): Boolean {
        verifyOwner()
        return !stopping && connected && session === membership.owner && admission?.memberId == membership.memberId &&
            admission?.bootstrap?.snapshot?.epoch == membership.epoch
    }
    // An admitted native scope survives metadata transport loss, but not an explicit replacement.
    fun canContinuePlayback(membership: RoomWatchMembership): Boolean = !stopping && currentAccount && playbackSession === membership.owner && playbackEpoch == membership.epoch
    companion object {
        fun isMetadataInterruption(error: Exception): Boolean {
            var reason: Throwable = error
            repeat(4) {
                if (reason is java.util.concurrent.CompletionException || reason is java.util.concurrent.ExecutionException) {
                    reason = reason.cause ?: return false
                }
            }
            return when (val failure = reason) {
                is RoomProtocolException -> failure.code in setOf("transport", "closed")
                is java.io.IOException, is TimeoutCancellationException -> true
                else -> false
            }
        }
    }
    suspend fun beginWatch(): Pair<RoomWatchMembership, RoomWatchContext> {
        verifyOwner()
        val owner = session ?: throw RoomProtocolException("closed")
        val value = admission ?: throw RoomProtocolException("closed")
        val member = value.bootstrap.snapshot.members.single { it.memberId == value.memberId }
        val membership = RoomWatchMembership(owner, value.memberId, member.identity, value.bootstrap.snapshot.epoch)
        return membership to watchContext(membership)
    }
    suspend fun watchContext(membership: RoomWatchMembership): RoomWatchContext {
        check(isCurrent(membership)) { "room-closed" }
        val value = membership.owner.watchContext()
        currentCoroutineContext().ensureActive(); check(isCurrent(membership)) { "room-closed" }
        recordWatch(value.bootstrap)
        return value
    }
    suspend fun performWatch(action: RoomCommandAction, membership: RoomWatchMembership, expected: RoomWatchContext): RoomWatchContext {
        check(isCurrent(membership)) { "room-closed" }
        val value = membership.owner.perform(action, expected)
        currentCoroutineContext().ensureActive(); check(isCurrent(membership)) { "room-closed" }
        recordWatch(value.bootstrap)
        return value
    }
    suspend fun publishPrepared(media: MediaDescriptor, membership: RoomWatchMembership): RoomPreparedPublication {
        check(isCurrent(membership) && media.fullHashSha256 != null && (media.durationMs ?: 0) > 0)
        val lease = java.util.UUID.randomUUID(); prepared = lease to media; localRevision = java.util.UUID.randomUUID(); startPublisher()
        try {
            while (true) {
                currentCoroutineContext().ensureActive()
                check(isCurrent(membership) && prepared?.first == lease) { "room-closed" }
                acknowledgedPrepared?.takeIf { it.lease == lease }?.let { return it }
                val task = publisher ?: throw RoomProtocolException("unavailable")
                task.join()
            }
        } catch (error: Exception) { clearPrepared(lease, membership); throw error }
    }
    fun clearPrepared(lease: java.util.UUID, membership: RoomWatchMembership) {
        if (!isCurrent(membership) || prepared?.first != lease) return
        prepared = null; localRevision = java.util.UUID.randomUUID(); startPublisher()
    }
    private fun startPublisher() {
        val owner = session ?: return
        if (stopping || !connected || publisher != null || localRevision == null || localRevision == acknowledgedRevision) return
        val task = scope.launch(start = CoroutineStart.LAZY) { publishLibrary(owner) }; publisher = task; task.start()
    }
    private suspend fun publishLibrary(owner: RoomMembershipSession) {
        try {
            while (currentCoroutineContext().isActive && !stopping && session === owner) {
                val revision = localRevision ?: return
                if (revision == acknowledgedRevision) return
                try {
                    val loaded = prepared
                    val publication = catalogMap.prepare(localFiles, loaded?.second)
                    editor = editor.copy(status = "roomCatalogPublishing")
                    val view = owner.publish(publication.items)
                    currentCoroutineContext().ensureActive(); if (stopping || session !== owner) return
                    catalogMap.acknowledge(publication); acknowledgedRevision = revision; accept(view)
                    acknowledgedPrepared = loaded?.let { entry -> RoomPreparedPublication(entry.first, publication.items.single { it.mediaId == publication.localIds[entry.second.mediaId] }) }
                    if (editor.status in setOf("roomCatalogPublishing", "roomCatalogPublishFailed")) editor = editor.copy(status = "")
                } catch (_: Exception) {
                    currentCoroutineContext().ensureActive()
                    if (stopping || session !== owner) return
                    if (localRevision != revision) continue
                    editor = editor.copy(status = "roomCatalogPublishFailed"); return
                }
            }
        } finally { publisher = null }
    }
    private fun accept(view: RoomCatalogView) {
        recordWatch(view.bootstrap)
        if (view.bootstrap.snapshot.sequence < (bootstrap?.snapshot?.sequence ?: 0)) return
        catalogView = view; editor = editor.copy(memberCount = view.bootstrap.snapshot.members.size); startMissingReport()
    }
    private fun recordWatch(bootstrap: RoomBootstrap) {
        if (bootstrap.snapshot.sequence >= (watchBootstrap?.snapshot?.sequence ?: 0)) watchBootstrap = bootstrap
    }
    private suspend fun run(values: Map<String, String>, owner: Boolean, authority: RoomAccountAuthority?) {
        var current: RoomClient? = null; var admitted: RoomAdmission? = null
        var failed = false; var interrupted = false
        var pending: AccountRoomAdmission? = null
        var failureNotice = if (authority == null) "roomConnectionFailed" else "roomRequestUnavailable"
        try {
            val next = authority?.let(::RoomClient) ?: RoomClient(RoomAddress(values.getValue("serviceAddress"))); current = next
            val value: RoomAdmission
            val identity: RoomIdentity
            if (authority != null) {
                val attempt = AccountRoomAdmission(next, owner, values); pending = attempt
                value = attempt.perform { editor = editor.copy(state = "awaitingApproval") }
                identity = value.bootstrap.snapshot.members.first { it.memberId == value.memberId }.identity
            } else {
                identity = RoomIdentity("run-" + java.util.UUID.randomUUID().toString(), values.getValue("username"))
                value = if (owner) next.create(identity, RoomConnection(values.getValue("syncplayHost"), values.getValue("syncplayPort").toInt(), values.getValue("roomName")), RoomSecret.fromInput(values.getValue("creationKey")))
                    else next.join(values.getValue("roomID"), RoomSecret.fromInput(values.getValue("invitation")), identity)
            }
            admitted = value; pending = null; currentCoroutineContext().ensureActive();
            if (!currentAccount) throw RoomProtocolException("closed")
            admission = value; editor = editor.clearSecrets()
            val ownerSession = RoomMembershipSession(next, value, identity)
            session = ownerSession; catalogMap = RoomLocalCatalog(); acknowledgedRevision = null; prepared = null; acknowledgedPrepared = null
            RoomEventMonitor.run(next.address, ownerSession, authority = authority,
                reconnecting = { editor = editor.copy(state = "reconnecting") },
                accepted = { view ->
                    playbackSession = ownerSession; playbackEpoch = value.bootstrap.snapshot.epoch
                    editor = editor.copy(state = "connected", roomId = value.roomId, owner = owner)
                    accept(view); startApprovals(ownerSession); startPublisher()
                })
        } catch (error: CancellationException) {
            // A bounded receive/page timeout is an attempt failure; owner cancellation still propagates.
            currentCoroutineContext().ensureActive()
            failed = true; interrupted = isMetadataInterruption(error) && playbackSession != null
            if (!interrupted) { playbackSession = null; playbackEpoch = null }
            editor = editor.copy(state = "connecting")
        }
        catch (error: Exception) {
            if (error is RoomAdmissionFailure) failureNotice = error.notice
            failed = true; interrupted = isMetadataInterruption(error) && playbackSession != null
            if (!interrupted) { playbackSession = null; playbackEpoch = null }
            editor = editor.copy(state = "connecting")
        }
        finally {
            withContext(NonCancellable) {
                approvals?.close(); approvals = null
                editor = editor.copy(pendingRequests = emptyList(), pendingStatus = "", deciding = false, pendingAvailable = false)
                missingReport?.cancelAndJoin(); missingReport = null
                publisher?.cancelAndJoin(); publisher = null; session = null; acknowledgedRevision = null; prepared = null; acknowledgedPrepared = null; catalogMap = RoomLocalCatalog()
                watchBootstrap = null
                current?.let { target ->
                    if (!interrupted) withTimeoutOrNull(3000) { runCatching { admitted?.let { target.leave(it) } ?: pending?.cancel() } }
                    target.close()
                }
                admission = null; catalogView = null; editor = editor.clearSecrets().copy(memberCount = 0, owner = false); work = null
                if (failed) editor = editor.copy(state = "failed", status = failureNotice)
            }
        }
    }
}
