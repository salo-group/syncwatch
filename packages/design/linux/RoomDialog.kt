package syncwatch.design

import androidx.compose.runtime.*

data class RoomAdmissionRow(val id: String, val name: String, val email: String)

/** Memory-only room draft and approval presentation; never persists credentials. */
class RoomEditor private constructor(initial: Map<String, String>, val mode: String, val state: String, val status: String,
    val roomId: String, val memberCount: Int, val owner: Boolean, val flow: String, val accountReady: Boolean,
    val accountName: String, val accountService: String, pendingRequests: List<RoomAdmissionRow>, val pendingAvailable: Boolean,
    val deciding: Boolean, val pendingStatus: String) {
    val values: Map<String, String> = java.util.Collections.unmodifiableMap(HashMap(initial))
    val pendingRequests: List<RoomAdmissionRow> = java.util.Collections.unmodifiableList(pendingRequests.toList())
    constructor(initial: Map<String, String> = emptyMap(), flow: String = SyncWatchRoomForm.defaultFlow) : this(
        SyncWatchRoomForm.defaults + initial.filterKeys { id -> SyncWatchRoomForm.fields.any { it.id == id && it.kind != "secret" } },
        "createRoom", "disconnected", "", "", 0, false, flow, false, "", "", emptyList(), true, false, "")
    val requiresAccount: Boolean get() = flow in SyncWatchRoomForm.signInFlows
    private val needsAccount: Boolean get() = requiresAccount && !accountReady
    val busy: Boolean get() = state in listOf("connecting", "reconnecting", "awaitingApproval")
    val connected: Boolean get() = state == "connected"
    val fields: List<SyncWatchRoomFormField> get() = SyncWatchRoomForm.fields.filter {
        it.id in SyncWatchRoomForm.flows[flow].orEmpty() && (it.section == "connection" || it.section == mode) }
    val isValid: Boolean get() = SyncWatchRoomForm.flows.containsKey(flow) && !needsAccount && fields.all { field -> values[field.id]?.let { value ->
        value.isNotEmpty() && value.length <= field.maxLength && value.none { it.code < 32 || it.code == 127 } } == true }
    fun copy(state: String = this.state, status: String = this.status, roomId: String = this.roomId, memberCount: Int = this.memberCount,
        owner: Boolean = this.owner, accountReady: Boolean = this.accountReady, accountName: String = this.accountName,
        accountService: String = this.accountService, pendingRequests: List<RoomAdmissionRow> = this.pendingRequests,
        pendingAvailable: Boolean = this.pendingAvailable, deciding: Boolean = this.deciding, pendingStatus: String = this.pendingStatus) =
        RoomEditor(values, mode, state, status, roomId, memberCount, owner, flow, accountReady, accountName, accountService, pendingRequests, pendingAvailable, deciding, pendingStatus)
    private fun form(values: Map<String, String>, mode: String = this.mode, status: String = this.status) = RoomEditor(
        values, mode, state, status, roomId, memberCount, owner, flow, accountReady, accountName, accountService, pendingRequests, pendingAvailable, deciding, pendingStatus)
    fun edit(id: String, value: String): RoomEditor {
        if (busy || connected || needsAccount || fields.none { it.id == id && value.length <= it.maxLength }) return this
        return form(values + (id to value), status = "")
    }
    fun selectMode(mode: String): RoomEditor = if (busy || connected || needsAccount || mode == this.mode || mode !in listOf("createRoom", "joinRoom")) this else
        form(clearSecrets().values, mode, "")
    fun clearSecrets(): RoomEditor = form(values + SyncWatchRoomForm.fields.filter { it.kind == "secret" }.associate { it.id to "" })
    val context: DesignContext get() = DesignContext(mapOf(
        "namespace" to "room", "showForm" to (!connected && state !in listOf("reconnecting", "awaitingApproval") && !needsAccount).toString(),
        "needsAccount" to needsAccount.toString(), "showAccountIdentity" to (requiresAccount && accountReady).toString(),
        "accountName" to accountName, "accountService" to accountService,
        "showPendingRequests" to (connected && owner && requiresAccount).toString(), "pendingStatus" to pendingStatus,
        "connected" to (connected || state == "reconnecting").toString(), "createMode" to (mode == "createRoom").toString(),
        "joinMode" to (mode == "joinRoom").toString(), "connectLabel" to mode,
        "status" to (SyncWatchRoomForm.statusCopy[state] ?: status), "roomID" to roomId, "memberCount" to memberCount.toString(),
        "can.close" to "true", "can.openAccount" to (needsAccount && !busy).toString(), "can.cancelRequest" to (state == "awaitingApproval").toString(),
        "can.chooseRoomMode" to (!busy && !connected && !needsAccount).toString(), "can.connectRoom" to (!busy && !connected && isValid).toString(),
        "can.leaveRoom" to (connected || state == "failed" || state == "reconnecting").toString(),
        "can.copyInvitation" to (connected && owner).toString(), "can.pasteInvitation" to (!busy && !connected && mode == "joinRoom" && !needsAccount).toString()),
        mapOf("fields" to fields.map { DesignContext(mapOf("id" to it.id, "label" to it.label, "value" to values[it.id].orEmpty(), "secret" to (it.kind == "secret").toString(), "editable" to (!busy).toString())) },
            "pendingRequests" to pendingRequests.map { DesignContext(mapOf("requestID" to it.id, "name" to it.name, "email" to it.email,
                "can.decide" to (connected && owner && pendingAvailable && !deciding).toString())) }))
}
@Composable fun RoomDialog(editor: RoomEditor, onEditorChange: (RoomEditor) -> Unit, language: String = "en", theme: SyncWatchTheme = SyncWatchTheme.dark,
    onAdmissionDecision: (String, Boolean) -> Unit = { _, _ -> }, onAction: (String) -> Unit = {}) {
    DesignSurface("Room", editor.context, language, theme) { event -> when (event.action) {
        "editRoomField" -> if (event.payload != null && event.value != null) onEditorChange(editor.edit(event.payload, event.value))
        "chooseRoomMode" -> event.payload?.let { onEditorChange(editor.selectMode(it)) }
        "approveRoomAdmission", "denyRoomAdmission" -> event.payload?.let { onAdmissionDecision(it, event.action == "approveRoomAdmission") }
        else -> onAction(event.action)
    } }
}
