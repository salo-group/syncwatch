package syncwatch.room

import java.util.UUID

data class RoomCallStatus(val state: String, val callId: String?, val generation: Long, val meetUri: String?, val reason: String?,
    val authorizationRequired: Boolean, val pollAfterMs: Long) {
    override fun toString(): String = "[room call state redacted]"
    companion object {
        fun decode(bytes: ByteArray): RoomCallStatus {
            val value = RoomWire.parse(bytes)
            RoomWire.keys(value, "status", "callId", "generation", "meetUri", "reason", "authorizationRequired", "pollAfterMs")
            val state = RoomWire.string(value["status"]); val id = RoomWire.optionalString(value["callId"])
            val generation = RoomWire.integer(value["generation"]); val uri = RoomWire.optionalString(value["meetUri"])
            val reason = RoomWire.optionalString(value["reason"]); val permission = value["authorizationRequired"] as? Boolean ?: throw RoomWire.bad()
            val poll = RoomWire.integer(value["pollAfterMs"], 10000)
            if (poll < 1000 || state !in listOf("idle", "creating", "ready", "creationFailed", "creationUnknown", "ending", "ended", "endFailed", "endUnknown", "unavailable") ||
                (id == null) != (state == "idle") || (generation == 0L) != (state == "idle") || (uri != null) != (state == "ready")) throw RoomWire.bad()
            if (id != null) AccountClient.token(id)
            RoomWire.validateMeetUri(uri)
            if (state in listOf("creationFailed", "endFailed")) { if (reason !in listOf("permissionRequired", "providerBusy", "providerUnavailable", "providerResponseInvalid", "providerPolicyInvalid", "roomChanged")) throw RoomWire.bad() }
            else if (state in listOf("creationUnknown", "endUnknown")) { if (reason != "providerOutcomeUnknown") throw RoomWire.bad() }
            else if (state == "unavailable") { if (reason != "roomChanged") throw RoomWire.bad() }
            else if (reason != null) throw RoomWire.bad()
            return RoomCallStatus(state, id, generation, uri, reason, permission, poll)
        }
    }
}
class RoomCallOperation internal constructor(admission: RoomAdmission, internal val scope: UUID, internal val action: String, status: RoomCallStatus?) {
    internal val requestId = AccountClient.secret()
    internal val roomId = admission.roomId
    internal val memberId = admission.memberId
    internal val epoch = admission.bootstrap.snapshot.epoch
    internal val callId = status?.callId
    internal val generation = status?.generation ?: 0L
    override fun toString(): String = "[room call operation redacted]"
}
suspend fun RoomClient.callStatus(admission: RoomAdmission): RoomCallStatus {
    needAccount()
    return RoomCallStatus.decode(request("GET", "v2/rooms/${admission.roomId}/call", admission = admission))
}
fun RoomClient.callStartOperation(admission: RoomAdmission): RoomCallOperation = callOperation(admission, "start", null)
fun RoomClient.callEndOperation(admission: RoomAdmission, status: RoomCallStatus): RoomCallOperation {
    if (status.callId == null || status.generation <= 0 || status.state !in listOf("ready", "endFailed")) throw RoomWire.bad()
    return callOperation(admission, "end", status)
}
private fun RoomClient.callOperation(admission: RoomAdmission, action: String, status: RoomCallStatus?): RoomCallOperation {
    val authority = needAccount(); if (admission.accountScope != authority.scope) throw RoomWire.bad()
    return RoomCallOperation(admission, authority.scope, action, status)
}
suspend fun RoomClient.requestCall(operation: RoomCallOperation, admission: RoomAdmission): RoomCallStatus {
    val authority = needAccount()
    if (operation.scope != authority.scope || operation.roomId != admission.roomId || operation.memberId != admission.memberId || operation.epoch != admission.bootstrap.snapshot.epoch) throw RoomWire.bad()
    val body = mutableMapOf<String, Any?>("v" to 2L, "requestId" to operation.requestId)
    if (operation.callId != null) { body["callId"] = operation.callId; body["generation"] = operation.generation }
    return RoomCallStatus.decode(request("POST", "v2/rooms/${admission.roomId}/call/${operation.action}", admission = admission, body = RoomWire.encode(body)))
}
