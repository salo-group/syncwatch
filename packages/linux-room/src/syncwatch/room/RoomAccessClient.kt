package syncwatch.room

private fun RoomClient.needAccount(): RoomAccountAuthority = authority ?: throw RoomWire.bad()

suspend fun RoomClient.create(run: String, requestId: String): RoomAdmission {
    val owner = needAccount(); RoomWire.id(run); RoomWire.id(requestId)
    val body = RoomWire.encode(mapOf("v" to 2L, "clientRunId" to run, "requestId" to requestId))
    return RoomAccessWire.admission(request("POST", "v2/rooms", body = body), run, owner.scope)
}
suspend fun RoomClient.invite(admission: RoomAdmission): RoomInvitation {
    needAccount()
    val value = RoomWire.parse(request("POST", "v2/rooms/${admission.roomId}/invitation", admission = admission))
    RoomWire.keys(value, "roomId", "invitation", "expiresIn")
    if (value["roomId"] != admission.roomId || RoomWire.integer(value["expiresIn"], 86400) == 0L) throw RoomWire.bad()
    return RoomInvitation(admission.roomId, RoomAccessWire.secret(value["invitation"]))
}
suspend fun RoomClient.requestAdmission(invitation: RoomInvitation, run: String, requestId: String): RoomAdmissionAttempt {
    val owner = needAccount(); RoomWire.id(run); RoomWire.id(requestId); RoomAccessWire.secret(invitation.credential.value)
    val body = RoomWire.encode(mapOf("v" to 2L, "clientRunId" to run, "requestId" to requestId, "invitation" to invitation.credential.value))
    val result = RoomAdmissionRequest.decode(RoomWire.parse(request("POST", "v2/rooms/${invitation.roomId}/admission-requests", body = body)))
    if (result.account.id != owner.accountId) throw RoomWire.bad()
    return RoomAdmissionAttempt(invitation.roomId, result, requestId, run, owner.scope)
}
private fun RoomClient.validate(attempt: RoomAdmissionAttempt): RoomAccountAuthority {
    val owner = needAccount(); if (attempt.scope != owner.scope) throw RoomWire.bad(); return owner
}
suspend fun RoomClient.admissionStatus(attempt: RoomAdmissionAttempt): RoomAdmissionRequest {
    val owner = validate(attempt)
    val result = RoomAdmissionRequest.decode(RoomWire.parse(request("GET", "v2/room-admissions/${attempt.requestId}", run = attempt.runId)))
    if (result.id != attempt.request.id || result.account.id != owner.accountId) throw RoomWire.bad()
    return result
}
suspend fun RoomClient.accept(attempt: RoomAdmissionAttempt): RoomAdmission {
    val owner = validate(attempt)
    val body = RoomWire.encode(mapOf("v" to 2L, "clientRunId" to attempt.runId))
    return RoomAccessWire.admission(request("POST", "v2/room-admissions/${attempt.requestId}/accept", body = body), attempt.runId, owner.scope, attempt.roomId)
}
suspend fun RoomClient.pending(admission: RoomAdmission): List<RoomAdmissionRequest> {
    needAccount()
    val items = RoomWire.value(request("GET", "v2/rooms/${admission.roomId}/pending", admission = admission)) as? List<*> ?: throw RoomWire.bad()
    if (items.size > 64) throw RoomWire.bad()
    val requests = items.map(RoomAdmissionRequest::decode)
    if (requests.map { it.id }.toSet().size != requests.size || requests.any { it.status != "pending" }) throw RoomWire.bad()
    return java.util.Collections.unmodifiableList(requests)
}
suspend fun RoomClient.decide(admission: RoomAdmission, approvalId: String, approve: Boolean) {
    needAccount(); RoomWire.id(approvalId)
    val body = RoomWire.encode(mapOf("v" to 2L, "approvalId" to approvalId, "approve" to approve))
    RoomAccessWire.status(request("POST", "v2/rooms/${admission.roomId}/decision", body = body, admission = admission), "decided")
}
suspend fun RoomClient.revoke(admission: RoomAdmission, memberId: String) {
    needAccount(); RoomWire.id(memberId)
    val body = RoomWire.encode(mapOf("v" to 2L, "memberId" to memberId))
    RoomAccessWire.status(request("POST", "v2/rooms/${admission.roomId}/revoke", body = body, admission = admission), "revoked")
}
suspend fun RoomClient.syncTicket(admission: RoomAdmission): RoomSyncTicket {
    needAccount()
    val value = RoomWire.parse(request("POST", "v2/rooms/${admission.roomId}/sync-ticket", admission = admission))
    RoomWire.keys(value, "ticket", "expiresIn")
    val seconds = RoomWire.integer(value["expiresIn"], 60); if (seconds == 0L) throw RoomWire.bad()
    return RoomSyncTicket(RoomAccessWire.secret(value["ticket"]), seconds)
}
