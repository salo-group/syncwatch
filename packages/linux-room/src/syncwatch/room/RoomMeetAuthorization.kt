package syncwatch.room

import java.net.URI
import java.util.UUID

class RoomMeetAuthorization internal constructor(val browserUri: URI, val expiresIn: Long, val pollAfterMs: Long,
    internal val id: String, internal val proof: String, admission: RoomAdmission, internal val scope: UUID) {
    internal val confirmation = AccountClient.secret()
    internal val roomId = admission.roomId
    internal val memberId = admission.memberId
    internal val epoch = admission.bootstrap.snapshot.epoch
    override fun toString(): String = "[Meet authorization redacted]"
}
data class RoomMeetAuthorizationStatus(val state: String, val account: AccountProfile?, val reason: String?) {
    companion object {
        fun decode(bytes: ByteArray, accountId: String): RoomMeetAuthorizationStatus {
            val value = RoomWire.parse(bytes); RoomWire.keys(value, "status", "account", "reason")
            val state = RoomWire.string(value["status"]); val reason = value["reason"]?.let(RoomWire::string)
            val account = value["account"]?.let {
                val profile = RoomWire.obj(it); RoomWire.keys(profile, "id", "name", "email")
                AccountClient.profile(profile)
            }
            if (state !in listOf("pending", "authorizing", "browserConfirmationRequired", "confirmAuthorization", "complete", "cancelled", "failed", "expired") ||
                (account != null) != (state == "confirmAuthorization") || account != null && account.id != accountId) throw RoomWire.bad()
            if (state == "failed") { if (reason !in listOf("roomChanged", "accountMismatch", "permissionUnavailable", "invalidConfirmation")) throw RoomWire.bad() }
            else if (state in listOf("browserConfirmationRequired", "confirmAuthorization")) { if (reason != null && reason != "invalidConfirmation") throw RoomWire.bad() }
            else if (reason != null) throw RoomWire.bad()
            return RoomMeetAuthorizationStatus(state, account, reason)
        }
    }
}
suspend fun RoomClient.beginMeetAuthorization(admission: RoomAdmission): RoomMeetAuthorization {
    val authority = needAccount(); val proof = AccountClient.secret()
    val body = RoomWire.encode(mapOf("v" to 2L, "challenge" to AccountClient.challenge(proof)))
    val value = RoomWire.parse(request("POST", "v2/rooms/${admission.roomId}/call-authorizations", admission = admission, body = body))
    RoomWire.keys(value, "transactionId", "browserUri", "expiresIn", "pollAfterMs")
    val id = AccountClient.token(value["transactionId"]); val uri = address.endpoint("auth/google/meet-start?id=$id")
    val expires = RoomWire.integer(value["expiresIn"], 600); val poll = RoomWire.integer(value["pollAfterMs"], 10000)
    if (value["browserUri"] != uri.toASCIIString() || expires == 0L || poll < 1000) throw RoomWire.bad()
    return RoomMeetAuthorization(uri, expires, poll, id, proof, admission, authority.scope)
}
suspend fun RoomClient.meetAuthorizationStatus(flow: RoomMeetAuthorization, admission: RoomAdmission) = meetAuthorization("poll", flow, admission)
suspend fun RoomClient.verifyMeetAuthorization(flow: RoomMeetAuthorization, admission: RoomAdmission, code: String) = meetAuthorization("verify", flow, admission, code)
suspend fun RoomClient.confirmMeetAuthorization(flow: RoomMeetAuthorization, admission: RoomAdmission, code: String) = meetAuthorization("confirm", flow, admission, code)
suspend fun RoomClient.cancelMeetAuthorization(flow: RoomMeetAuthorization, admission: RoomAdmission) = meetAuthorization("cancel", flow, admission)
private suspend fun RoomClient.meetAuthorization(action: String, flow: RoomMeetAuthorization, admission: RoomAdmission, code: String? = null): RoomMeetAuthorizationStatus {
    val authority = needAccount()
    if (flow.scope != authority.scope || flow.roomId != admission.roomId || flow.memberId != admission.memberId || flow.epoch != admission.bootstrap.snapshot.epoch) throw RoomWire.bad()
    val body = mutableMapOf<String, Any?>("v" to 2L, "proof" to flow.proof)
    if (code != null) { if (!code.matches(Regex("[0-9A-F]{12}"))) throw RoomWire.bad(); body["browserProof"] = code }
    if (action == "confirm") body["requestId"] = flow.confirmation
    return RoomMeetAuthorizationStatus.decode(request("POST", "v2/call-authorizations/${flow.id}/$action", admission = admission, body = RoomWire.encode(body)), authority.accountId)
}
