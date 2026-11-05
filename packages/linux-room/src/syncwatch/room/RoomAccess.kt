package syncwatch.room

import java.time.OffsetDateTime
import java.util.UUID
import syncwatch.ipc.Json

class RoomAdmissionRequest(val id: String, val status: String, val account: AccountProfile) {
    companion object {
        internal fun decode(input: Any?): RoomAdmissionRequest {
            val value = RoomWire.obj(input); RoomWire.keys(value, "id", "status", "accountId", "name", "email")
            val id = RoomWire.string(value["id"]); RoomWire.id(id)
            val status = RoomWire.string(value["status"])
            if (status !in setOf("pending", "approved", "denied", "admitted", "expired", "revoked", "closed")) throw RoomWire.bad()
            val account = AccountProfile(RoomWire.string(value["accountId"]), RoomWire.string(value["name"]), RoomWire.string(value["email"]))
            if (!account.id.matches(Regex("[A-Za-z0-9_-]{43}"))) throw RoomWire.bad()
            RoomWire.text(account.name, 128); RoomWire.text(account.email, 320)
            return RoomAdmissionRequest(id, status, account)
        }
    }
}

/** Guest correlation is distinct from the owner's opaque approval identifier. */
class RoomAdmissionAttempt internal constructor(val roomId: String, val request: RoomAdmissionRequest,
    internal val requestId: String, internal val runId: String, internal val scope: UUID)

class RoomSyncTicket(val credential: RoomSecret, val expiresIn: Long)

internal object RoomAccessWire {
    fun result(bytes: ByteArray): ByteArray {
        val value = RoomWire.parse(bytes); RoomWire.keys(value, "v", "result")
        if (RoomWire.integer(value["v"], 2) != 2L) throw RoomWire.bad()
        val result = value["result"]
        if (result !is Map<*, *> && result !is List<*>) throw RoomWire.bad()
        return RoomWire.encode(result)
    }
    fun status(bytes: ByteArray, expected: String) {
        val value = RoomWire.parse(bytes); RoomWire.keys(value, "status")
        if (value["status"] != expected) throw RoomWire.bad()
    }
    fun secret(input: Any?): RoomSecret {
        val value = RoomWire.string(input)
        if (!value.matches(Regex("[0-9A-F]{64}"))) throw RoomWire.bad()
        return RoomSecret(value)
    }
    fun admission(bytes: ByteArray, run: String, scope: UUID, expectedRoom: String? = null): RoomAdmission {
        val value = RoomWire.parse(bytes); RoomWire.keys(value, "roomId", "memberId", "expiresAt", "bootstrap")
        val room = RoomWire.string(value["roomId"]); val member = RoomWire.string(value["memberId"])
        RoomWire.id(room); RoomWire.id(member)
        val bootstrap = RoomWire.bootstrap(value["bootstrap"])
        if (expectedRoom != null && expectedRoom != room || bootstrap.snapshot.members.firstOrNull { it.memberId == member }?.identity?.clientRunId != run) throw RoomWire.bad()
        val time = RoomWire.string(value["expiresAt"])
        if (time.length > 64) throw RoomWire.bad()
        val expires = try { OffsetDateTime.parse(time) } catch (_: Exception) { throw RoomWire.bad() }
        return RoomAdmission(room, member, null, null, expires, bootstrap).also { it.accountScope = scope }
    }
}
