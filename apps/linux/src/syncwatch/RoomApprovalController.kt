package syncwatch

import kotlinx.coroutines.*
import syncwatch.design.RoomAdmissionRow

internal data class RoomApprovalPresentation(val rows: List<RoomAdmissionRow> = emptyList(), val available: Boolean = false,
    val deciding: Boolean = false, val notice: String = "")

/** Independent owner-only side work; a failed list refresh does not end a watch. */
internal class RoomApprovalController(private val membership: RoomMembershipSession, private val scope: CoroutineScope,
    private val changed: (RoomApprovalPresentation) -> Unit) {
    private var state = RoomApprovalPresentation()
    private var poll: Job? = null
    private var decision: Job? = null
    private var revision = 0L
    private var closed = false
    private fun publish() { if (!closed) changed(state) }
    fun start() {
        if (poll != null || closed) return
        publish()
        val job = scope.launch(start = CoroutineStart.LAZY) { while (isActive) { read(); delay(3_000) } }
        poll = job; job.start()
    }
    private suspend fun read() {
        val expected = revision
        try {
            val values = membership.pendingAdmissions().map { RoomAdmissionRow(it.id, it.account.name, it.account.email) }
            currentCoroutineContext().ensureActive()
            if (closed || revision != expected) return
            val modified = state.rows != values || !state.available || state.notice.isNotEmpty()
            state = state.copy(rows = values, available = true, notice = "")
            if (modified) publish()
        } catch (_: Exception) {
            currentCoroutineContext().ensureActive()
            if (closed || revision != expected) return
            state = state.copy(available = false, notice = "roomPendingUnavailable"); publish()
        }
    }
    fun decide(id: String, approve: Boolean) {
        if (closed || decision != null || !state.available || state.rows.none { it.id == id }) return
        revision++; state = state.copy(deciding = true); publish()
        val job = scope.launch(start = CoroutineStart.LAZY) {
            try { membership.decideAdmission(id, approve) }
            catch (_: Exception) { currentCoroutineContext().ensureActive(); if (!closed) state = state.copy(notice = "roomAdmissionChanged") }
            if (closed) return@launch
            revision++; read()
            if (closed) return@launch
            state = state.copy(deciding = false); decision = null; publish()
        }
        decision = job; job.start()
    }
    suspend fun close() = withContext(NonCancellable) {
        closed = true; poll?.cancel(); decision?.cancel(); poll?.join(); decision?.join(); poll = null; decision = null
    }
}
