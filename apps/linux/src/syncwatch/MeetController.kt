package syncwatch

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.net.URI
import kotlinx.coroutines.*
import syncwatch.design.LibraryPresentation
import syncwatch.design.MeetPresentation
import syncwatch.room.*

/** Room-owned permission and explicit browser handoff, independent of watch/player ownership. */
internal class MeetController(private val scope: CoroutineScope, private val room: RoomController,
    private val openBrowser: suspend (URI) -> Boolean = { uri -> withContext(Dispatchers.IO) {
        try { java.awt.Desktop.getDesktop().browse(uri); true } catch (_: Exception) { false }
    } }) {
    private var model by mutableStateOf(MeetPresentation())
    private var membership: RoomCallMembership? = null
    private var call: RoomCallStatus? = null
    private var permission: RoomMeetAuthorization? = null
    private var permissionStatus: RoomMeetAuthorizationStatus? = null
    private var operation: RoomCallOperation? = null
    private var endTarget: RoomCallStatus? = null
    private var work: Job? = null
    private var cleanup: Job? = null
    private var generation = 0L
    private var visible = false
    private var closing = false
    private var busy = false
    private var browserOpened = false
    private var notice: String? = null
    val presentation: MeetPresentation get() {
        if (closing) return MeetPresentation(enabledActions = emptySet())
        val member = membership ?: return MeetPresentation()
        return if (room.isCurrent(member)) model else MeetPresentation()
    }
    fun libraryPresentation(source: LibraryPresentation): LibraryPresentation = if (closing) source else source.copy(enabledActions = source.enabledActions + "openCall")
    fun membershipChanged() {
        if (closing) return
        val next = room.callMembership(); val current = membership
        if (next != null && current != null && next.owner === current.owner && next.memberId == current.memberId && next.epoch == current.epoch) return
        if (next == null && current == null) return
        invalidate(next); if (visible && next != null) launch { _, _ -> }
    }
    fun present() {
        if (closing) return
        visible = true; membershipChanged()
        if (membership != null && work?.isActive != true) launch { _, _ -> }
    }
    suspend fun dismiss() { visible = false; invalidate(membership); withContext(NonCancellable) { cleanup?.join() } }
    suspend fun close() { closing = true; visible = false; invalidate(null); withContext(NonCancellable) { cleanup?.join() } }
    private fun invalidate(next: RoomCallMembership?) {
        generation++
        val previous = work; val priorCleanup = cleanup; val flow = permission; val old = membership
        previous?.cancel(); work = null; membership = next
        call = null; permission = null; permissionStatus = null; operation = null; endTarget = null
        notice = null; busy = false; browserOpened = false; model = MeetPresentation()
        cleanup = scope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) {
                priorCleanup?.join(); previous?.join()
                if (flow != null && old != null) try { old.owner.cancelMeetAuthorization(flow) } catch (_: Exception) { }
            }
        }
    }
    fun action(action: String, value: String? = null) {
        val current = membership ?: return
        if (!visible || action !in presentation.enabledActions || !room.isCurrent(current)) return
        if (action == "editMeetBrowserProof") { if (value != null && value.length <= 32) { model = model.copy(browserProof = value); render() }; return }
        if (action == "meetEnd") { endTarget = call; render(); return }
        if (action == "meetCancelEnd") { endTarget = null; render(); return }
        launch { member, token ->
            when (action) {
                "meetAuthorize" -> {
                    val flow = member.owner.beginMeetAuthorization()
                    if (!valid(token, member)) { try { member.owner.cancelMeetAuthorization(flow) } catch (_: Exception) { }; return@launch }
                    permission = flow; permissionStatus = null; browserOpened = false; model = model.copy(browserProof = "")
                }
                "meetOpenAuthorization" -> {
                    val flow = permission ?: return@launch
                    val accepted = openBrowser(flow.browserUri)
                    if (!valid(token, member)) return@launch
                    browserOpened = accepted; if (!accepted) notice = "meetBrowserUnavailable"
                }
                "meetVerify", "meetConfirm" -> {
                    val flow = permission ?: return@launch; val code = model.browserProof.trim().uppercase(java.util.Locale.ROOT)
                    val result = if (action == "meetVerify") member.owner.verifyMeetAuthorization(flow, code) else member.owner.confirmMeetAuthorization(flow, code)
                    if (!valid(token, member)) return@launch; apply(result)
                }
                "meetCancelAuthorization" -> {
                    permission?.let { member.owner.cancelMeetAuthorization(it) }
                    if (!valid(token, member)) return@launch
                    permission = null; permissionStatus = null; browserOpened = false; model = model.copy(browserProof = "")
                }
                "meetStart" -> {
                    val next = member.owner.callStartOperation(); operation = next
                    val result = member.owner.requestCall(next)
                    if (!valid(token, member)) return@launch; apply(result)
                }
                "meetConfirmEnd" -> {
                    val target = endTarget ?: return@launch
                    if (call?.callId != target.callId || call?.generation != target.generation) return@launch
                    val next = member.owner.callEndOperation(target); operation = next; endTarget = null
                    val result = member.owner.requestCall(next)
                    if (!valid(token, member)) return@launch; apply(result)
                }
                "meetOpen" -> {
                    val observed = call ?: return@launch
                    if (observed.state != "ready" || observed.meetUri == null) return@launch
                    val fresh = member.owner.callStatus()
                    if (!valid(token, member)) return@launch
                    apply(fresh)
                    if (fresh.state != "ready" || fresh.callId != observed.callId || fresh.generation != observed.generation || fresh.meetUri != observed.meetUri) return@launch
                    val accepted = openBrowser(URI(observed.meetUri))
                    if (!valid(token, member) || call?.callId != observed.callId || call?.generation != observed.generation) return@launch
                    notice = if (accepted) "meetHandedOff" else "meetBrowserUnavailable"
                }
                "meetRetry" -> { call = null } // Recover by reads; never automatically repeat a mutation.
                else -> return@launch
            }
        }
    }
    private fun launch(body: suspend (RoomCallMembership, Long) -> Unit) {
        val member = membership ?: return
        if (!room.isCurrent(member) || closing) return
        val previous = work; val priorCleanup = cleanup; val token = ++generation
        previous?.cancel(); busy = true; notice = null; render()
        work = scope.launch {
            priorCleanup?.join(); previous?.join(); if (!valid(token, member)) return@launch
            try {
                body(member, token)
                if (!valid(token, member)) return@launch; busy = false; render()
                while (valid(token, member)) {
                    val wait: Long
                    val flow = permission
                    if (flow != null) {
                        val result = member.owner.meetAuthorizationStatus(flow)
                        if (!valid(token, member)) return@launch; apply(result); wait = flow.pollAfterMs
                    } else {
                        val result = member.owner.callStatus()
                        if (!valid(token, member)) return@launch; apply(result); wait = result.pollAfterMs
                    }
                    render(); delay(wait)
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (valid(token, member)) { busy = false; notice = if (error is RoomProtocolException && error.code == "malformed" && permission != null) "meetInvalidConfirmation" else "meetFailed"; render() }
            }
        }
    }
    private suspend fun valid(token: Long, member: RoomCallMembership): Boolean = currentCoroutineContext().isActive && !closing && visible && generation == token && room.isCurrent(member)
    private fun apply(value: RoomMeetAuthorizationStatus) {
        permissionStatus = value
        value.reason?.let { notice = when (it) { "accountMismatch" -> "meetAccountMismatch"; "invalidConfirmation" -> "meetInvalidConfirmation"; else -> "meetPermissionUnavailable" } }
        if (value.state in setOf("complete", "cancelled", "failed", "expired")) {
            permission = null; permissionStatus = null; model = model.copy(browserProof = ""); browserOpened = false
            if (value.state == "complete") call = null
        }
    }
    private fun apply(value: RoomCallStatus) { if (call?.callId != value.callId || call?.generation != value.generation) { endTarget = null; notice = null }; call = value }
    private fun render() {
        val member = membership
        if (member == null || !room.isCurrent(member) || closing) { model = MeetPresentation(); return }
        var authorization = if (permission == null) "none" else permissionStatus?.state ?: "pending"
        if (browserOpened && authorization == "pending") authorization = "authorizing"
        val account = if (authorization == "confirmAuthorization") permissionStatus?.account else null
        var status = if (busy) "meetWorking" else notice ?: if (permission != null) when (authorization) {
            "browserConfirmationRequired" -> "meetBrowserCode"; "confirmAuthorization" -> "meetPermissionConfirmation"; else -> "meetAuthorizing"
        } else when (call?.state) { "idle" -> "meetIdle"; "ready" -> "meetReady"; "creating", "ending" -> "meetWorking"; "creationUnknown", "endUnknown" -> "meetUnknown"; "ended" -> "meetEnded"; "unavailable" -> "meetUnavailable"; null -> "meetWorking"; else -> "meetFailed" }
        var actions = mutableSetOf("close")
        if (!busy) {
            if (permission != null) {
                actions += "meetCancelAuthorization"
                if (authorization == "pending") actions += "meetOpenAuthorization"
                if (authorization == "browserConfirmationRequired") { actions += "editMeetBrowserProof"; if (model.browserProof.isNotEmpty()) actions += "meetVerify" }
                if (authorization == "confirmAuthorization") actions += "meetConfirm"
            } else {
                val observed = call
                actions += "meetRetry"
                if (observed != null) {
                    if (member.isOwner && observed.authorizationRequired) actions += "meetAuthorize"
                    if (observed.state == "ready") actions += "meetOpen"
                    if (member.isOwner && !observed.authorizationRequired) {
                        if (observed.state in setOf("idle", "ended", "creationFailed")) actions += "meetStart"
                        if (observed.state in setOf("ready", "endFailed")) actions += "meetEnd"
                    }
                    val target = endTarget
                    if (target != null && target.callId == observed.callId && target.generation == observed.generation) {
                        status = "meetEndConfirmation"; actions = mutableSetOf("close", "meetConfirmEnd", "meetCancelEnd")
                    }
                }
            }
        }
        model = MeetPresentation(authorization, model.browserProof, account?.name ?: "", account?.email ?: "", status, actions)
    }
}
