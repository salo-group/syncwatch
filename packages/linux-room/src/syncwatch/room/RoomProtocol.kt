package syncwatch.room

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.time.OffsetDateTime
import syncwatch.ipc.Json

class RoomIdentity(val clientRunId: String, val syncplayUsername: String) {
    init { RoomWire.id(clientRunId); RoomWire.text(syncplayUsername, 64) }
    override fun equals(other: Any?): Boolean = other is RoomIdentity && clientRunId == other.clientRunId && syncplayUsername == other.syncplayUsername
    override fun hashCode(): Int = 31 * clientRunId.hashCode() + syncplayUsername.hashCode()
}
data class RoomMember(val memberId: String, val identity: RoomIdentity)
class RoomConnection(val host: String, val port: Int, val room: String) {
    init {
        RoomWire.text(host, 253); RoomWire.text(room, 64)
        if (port !in 1..65535 || !host.matches(Regex("[A-Za-z0-9.:-]+"))) throw RoomWire.bad()
    }
}
class RoomSnapshot(val epoch: String, val sequence: Long, val watchGeneration: Long, val watchStatus: String, val mediaId: String?,
    members: List<RoomMember>, val meetUri: String?) {
    val members: List<RoomMember> = java.util.Collections.unmodifiableList(members.toList())
}
class RoomBootstrap(val snapshot: RoomSnapshot, val connection: RoomConnection, val catalogCursor: String, missingMembers: List<String>) {
    val missingMembers: List<String> = java.util.Collections.unmodifiableList(missingMembers.toList())
    companion object {
        fun decode(bytes: ByteArray): RoomBootstrap = RoomWire.bootstrap(RoomWire.parse(bytes))
        fun initialEvent(bytes: ByteArray, admission: RoomAdmission): RoomBootstrap {
            val value = RoomWire.parse(bytes); RoomWire.keys(value, "v", "type", "data")
            if (RoomWire.integer(value["v"], 1) != 1L || value["type"] != "room.bootstrap") throw RoomWire.bad()
            val result = RoomWire.bootstrap(value["data"]); val expected = admission.bootstrap
            if (result.snapshot.epoch != expected.snapshot.epoch || result.connection.host != expected.connection.host ||
                result.connection.port != expected.connection.port || result.connection.room != expected.connection.room ||
                result.snapshot.members.firstOrNull { it.memberId == admission.memberId } != expected.snapshot.members.firstOrNull { it.memberId == admission.memberId }) throw RoomWire.bad()
            return result
        }
    }
}
class RoomAdmission(val roomId: String, val memberId: String, val token: RoomSecret?, val invitation: RoomSecret?,
    val expiresAt: OffsetDateTime, val bootstrap: RoomBootstrap) {
    internal var accountScope: java.util.UUID? = null
    val isAccountBacked: Boolean get() = accountScope != null
    companion object {
        fun decode(bytes: ByteArray, identity: RoomIdentity, owner: Boolean): RoomAdmission {
            val value = RoomWire.parse(bytes)
            RoomWire.keys(value, "roomId", "memberId", "token", "invitation", "expiresAt", "bootstrap")
            val room = RoomWire.string(value["roomId"]); val member = RoomWire.string(value["memberId"])
            RoomWire.id(room); RoomWire.id(member)
            val bootstrap = RoomWire.bootstrap(value["bootstrap"])
            if (bootstrap.snapshot.members.firstOrNull { it.memberId == member }?.identity != identity) throw RoomWire.bad()
            val invitation = RoomWire.optionalString(value["invitation"])
            if ((invitation != null) != owner) throw RoomWire.bad()
            val expires = try { OffsetDateTime.parse(RoomWire.string(value["expiresAt"])) } catch (_: Exception) { throw RoomWire.bad() }
            return RoomAdmission(room, member, RoomSecret(RoomWire.string(value["token"])), invitation?.let(::RoomSecret), expires, bootstrap)
        }
    }
}

internal object RoomWire {
    const val maximumBytes = 65536
    const val maximumCounter = 9007199254740991L
    fun bad() = RoomProtocolException("malformed")
    fun id(value: String) { if (!value.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,63}"))) throw bad() }
    fun text(value: String, maximum: Int) {
        if (value.codePointCount(0, value.length) !in 1..maximum || value.any { it.code < 32 || it.code == 127 }) throw bad()
        try { StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(value)) }
        catch (_: Exception) { throw bad() }
    }
    fun string(value: Any?): String = value as? String ?: throw bad()
    fun validateMeetUri(value: String?) {
        if (value != null && !value.matches(Regex("https://meet\\.google\\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}"))) throw bad()
    }
    fun optionalString(value: Any?): String? = if (value == null) null else string(value)
    fun obj(value: Any?): Map<String, Any?> {
        val map = value as? Map<*, *> ?: throw bad()
        if (map.keys.any { it !is String }) throw bad()
        return map.entries.associate { (key, item) -> (key as String) to item }
    }
    fun keys(value: Map<String, Any?>, vararg names: String) { if (value.keys != names.toSet()) throw bad() }
    fun integer(value: Any?, maximum: Long = maximumCounter): Long = (value as? Long)?.takeIf { it in 0..maximum } ?: throw bad()
    fun bootstrap(input: Any?): RoomBootstrap {
        val value = obj(input); keys(value, "snapshot", "connection", "catalogCursor", "missingMembers")
        val snapshot = snapshot(value["snapshot"]); val connection = obj(value["connection"])
        keys(connection, "host", "port", "room", "requireTls")
        val missing = (value["missingMembers"] as? List<*>)?.map(::string) ?: throw bad()
        if (connection["requireTls"] != true || missing.size > 64 || missing.toSet().size != missing.size ||
            missing.any { id -> snapshot.members.none { it.memberId == id } }) throw bad()
        val cursor = string(value["catalogCursor"]); text(cursor, 128)
        return RoomBootstrap(snapshot, RoomConnection(string(connection["host"]), integer(connection["port"], 65535).toInt(), string(connection["room"])), cursor, missing)
    }
    private fun snapshot(input: Any?): RoomSnapshot {
        val value = obj(input); keys(value, "v", "roomEpoch", "sequence", "watchGeneration", "watch", "members", "meetUri")
        val members = value["members"] as? List<*> ?: throw bad()
        if (integer(value["v"], 1) != 1L || members.size !in 1..64) throw bad()
        val epoch = string(value["roomEpoch"]); id(epoch)
        val bindings = members.map { item ->
            val member = obj(item); keys(member, "memberId", "clientRunId", "syncplayUsername")
            val memberId = string(member["memberId"]); id(memberId)
            RoomMember(memberId, RoomIdentity(string(member["clientRunId"]), string(member["syncplayUsername"])))
        }
        if (bindings.map { it.memberId }.toSet().size != bindings.size || bindings.map { it.identity.syncplayUsername }.toSet().size != bindings.size) throw bad()
        val watch = obj(value["watch"]); keys(watch, "status", "mediaId")
        val status = string(watch["status"]); val media = optionalString(watch["mediaId"])
        if (status !in listOf("idle", "selected", "active", "ended") || (media == null) != (status == "idle")) throw bad()
        media?.let(::id)
        val meet = optionalString(value["meetUri"])
        validateMeetUri(meet)
        return RoomSnapshot(epoch, integer(value["sequence"]), integer(value["watchGeneration"]), status, media, bindings, meet)
    }
    fun encode(value: Any?): ByteArray = Json.write(value).toByteArray(StandardCharsets.UTF_8).also {
        if (it.size > maximumBytes) throw RoomProtocolException("frameTooLarge")
    }
    fun parse(bytes: ByteArray): Map<String, Any?> = obj(value(bytes))
    fun value(bytes: ByteArray): Any? {
        if (bytes.size > maximumBytes) throw RoomProtocolException("frameTooLarge")
        try {
            val string = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
            val value = Json.parse(string); checkTree(value); return value
        } catch (error: RoomProtocolException) { throw error } catch (_: Exception) { throw bad() }
    }
    private fun checkTree(value: Any?) {
        when (value) {
            is Map<*, *> -> value.values.forEach(::checkTree)
            is List<*> -> value.forEach(::checkTree)
            is Number -> if (value !is Long || value !in 0..maximumCounter) throw bad()
        }
    }
}
