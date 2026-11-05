package syncwatch.bridge

import syncwatch.domain.PlaybackScope
import syncwatch.domain.WatchFailure
import syncwatch.ipc.Json

enum class HelperReplyKind { ready, applied, stopped }

sealed interface HelperReply {
    data class Ready(val revision: String) : HelperReply
    data object Applied : HelperReply
    data object Stopped : HelperReply
    data class Rejected(val code: String, val retryable: Boolean) : HelperReply

    companion object {
        fun decode(frame: ByteArray, wireScope: PlaybackScope, commandId: Long, expected: HelperReplyKind, expectedRevision: String? = null, requireAdmission: Boolean = false): HelperReply {
            if (commandId !in 1..PlaybackScope.MAXIMUM_GENERATION) bad()
            val value = PlayerCorrection.frameValue(frame)
            PlayerCorrection.keys(value, setOf("v", "replyTo", "type", "clientRunId", "mediaGeneration", "payload"))
            if (PlayerCorrection.integer(value["v"], 1) != 1L || value["replyTo"] != "cmd-$commandId") bad()
            wireScope.check(PlaybackScope(PlayerCorrection.text(value, "clientRunId"), PlayerCorrection.integer(value["mediaGeneration"], PlaybackScope.MAXIMUM_GENERATION)))
            val payload = Json.`object`(value["payload"])
            val kind = PlayerCorrection.text(value, "type")
            if (kind == "bridge.error") {
                PlayerCorrection.keys(payload, setOf("code", "message", "retryable"))
                val code = PlayerCorrection.text(payload, "code"); val message = PlayerCorrection.text(payload, "message")
                if (code !in errorCodes || message.codePointCount(0, message.length) !in 1..512 || message.any { it.code < 32 || it.code == 127 }) bad()
                return Rejected(code, payload["retryable"] as? Boolean ?: bad())
            }
            return when (expected) {
                HelperReplyKind.ready -> {
                    PlayerCorrection.keys(payload, setOf("syncplayRevision"), setOf("admissionVersion"))
                    if ("admissionVersion" in payload) { if (PlayerCorrection.integer(payload["admissionVersion"], 1) != 1L) bad() }
                    else if (requireAdmission) bad()
                    if (kind != "bridge.ready" || expectedRevision == null || !expectedRevision.matches(Regex("[0-9a-f]{40}")) || payload["syncplayRevision"] != expectedRevision) bad()
                    Ready(expectedRevision)
                }
                HelperReplyKind.applied -> {
                    PlayerCorrection.keys(payload, setOf("status"))
                    if (kind != "bridge.result" || payload["status"] != "applied") bad()
                    Applied
                }
                HelperReplyKind.stopped -> {
                    PlayerCorrection.keys(payload, emptySet())
                    if (kind != "bridge.stopped") bad()
                    Stopped
                }
            }
        }
        private fun bad(): Nothing = throw WatchFailure("malformed")
        private val errorCodes = setOf("malformed", "frameTooLarge", "unsupportedVersion", "foreignRun", "staleGeneration", "staleEpoch", "sequenceGap", "duplicate", "conflict", "invalidState", "queueFull", "unavailable", "timeout", "cancelled", "internal")
    }
}
