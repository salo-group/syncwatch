package syncwatch.bridge

import syncwatch.domain.PlaybackObservation
import syncwatch.domain.PlaybackScope
import syncwatch.domain.PlaybackStatus
import syncwatch.domain.WatchFailure
import syncwatch.domain.WatchSession
import syncwatch.ipc.Json

/** Encoding only. The session owner serializes writes and allocates IDs. */
class HelperCommands(private val wireScope: PlaybackScope) {
    fun hello(id: Long) = frame(id, "bridge.hello", mapOf("supportedVersions" to listOf(1L)))
    fun shutdown(id: Long) = frame(id, "bridge.shutdown", emptyMap())
    fun disconnect(id: Long) = frame(id, "sync.disconnect", emptyMap())
    fun ready(id: Long, value: Boolean) = frame(id, "sync.setReady", mapOf("ready" to value))
    fun connect(id: Long, host: String, port: Int, room: String, username: String, accountAdmission: Boolean = false): ByteArray {
        if (host.length !in 1..253 || !host.matches(Regex("[A-Za-z0-9.:_-]+")) || port !in 1..65535) bad()
        text(room, 256); text(username, 64)
        return frame(id, "sync.connect", mapOf("host" to host, "port" to port, "room" to room, "username" to username, "tls" to true) + if (accountAdmission) mapOf("accountAdmission" to true) else emptyMap())
    }
    fun admit(id: Long, nonce: String, authority: SyncplayAdmission): ByteArray {
        if (!nonce.matches(Regex("[0-9a-f]{32}"))) bad()
        return frame(id, "sync.admit", mapOf("nonce" to nonce, "ticket" to authority.credential, "clientRunId" to authority.clientRunId))
    }
    fun loaded(id: Long, handle: String, name: String, durationMs: Long, fileSizeBytes: Long): ByteArray {
        PlaybackScope.validateId(handle); text(name, 256)
        if (name.contains('/') || name.contains('\\') || durationMs !in 0..WatchSession.MAXIMUM_POSITION_MS || fileSizeBytes !in 0..PlaybackScope.MAXIMUM_GENERATION) bad()
        return frame(id, "player.loaded", mapOf("mediaHandle" to handle, "name" to name, "durationMs" to durationMs, "fileSizeBytes" to fileSizeBytes))
    }
    fun observation(id: Long, value: PlaybackObservation, nativeScope: PlaybackScope) = state(id, value, nativeScope, null)
    /** Only an explicit local shared action may publish user authority. */
    fun userObservation(id: Long, sequence: Long, value: PlaybackObservation, nativeScope: PlaybackScope): ByteArray {
        if (sequence !in 1..PlaybackScope.MAXIMUM_GENERATION || value.status != PlaybackStatus.ready) bad()
        return state(id, value, nativeScope, sequence)
    }
    private fun state(id: Long, value: PlaybackObservation, nativeScope: PlaybackScope, sequence: Long?): ByteArray {
        nativeScope.check(value.scope)
        return frame(id, "player.state", mapOf("status" to value.status.name, "positionMs" to value.positionMs,
            "durationMs" to value.durationMs, "paused" to value.paused, "rate" to value.rate, "seekable" to value.seekable), sequence)
    }
    private fun frame(id: Long, type: String, payload: Map<String, Any?>, sequence: Long? = null): ByteArray {
        if (id !in 1..PlaybackScope.MAXIMUM_GENERATION) bad()
        val envelope = linkedMapOf<String, Any?>("v" to 1L, "id" to "cmd-$id", "type" to type,
            "clientRunId" to wireScope.clientRunId, "mediaGeneration" to wireScope.mediaGeneration, "payload" to payload)
        if (type == "player.state") envelope["origin"] = if (sequence == null) "observation" else "user"
        if (sequence != null) envelope["actionSequence"] = sequence
        return (Json.write(envelope) + "\n").toByteArray(Charsets.UTF_8).also { if (it.size > 65536) bad() }
    }
    private fun text(value: String, maximum: Int) {
        if (value.codePointCount(0, value.length) !in 1..maximum || value.any { it.code < 32 || it.code == 127 }) bad()
        var index = 0
        while (index < value.length) {
            val c = value[index++]
            if (Character.isHighSurrogate(c)) { if (index == value.length || !Character.isLowSurrogate(value[index++])) bad() }
            else if (Character.isLowSurrogate(c)) bad()
        }
    }
    private fun bad(): Nothing = throw WatchFailure("malformed")
}
