package syncwatch.bridge

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import kotlin.math.abs
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import syncwatch.domain.PlaybackObservation
import syncwatch.domain.PlaybackPort
import syncwatch.domain.PlaybackScope
import syncwatch.domain.PlaybackStatus
import syncwatch.domain.WatchFailure
import syncwatch.ipc.Json

/** Validated helper request. Wire identity never becomes native ownership. */
class PlayerCorrection private constructor(
    val id: String, val afterUserAction: Long, val operation: Operation, private val wireScope: PlaybackScope,
) {
    internal fun sameRequest(other: PlayerCorrection) = id == other.id && afterUserAction == other.afterUserAction && operation == other.operation && wireScope == other.wireScope
    sealed interface Operation {
        data class Pause(val paused: Boolean) : Operation
        data class Seek(val positionMs: Long) : Operation
        data class Rate(val rate: Double) : Operation
        data class KeepLoaded(val handle: String) : Operation
    }

    /** The coordinator checks its action fence before and after this await. */
    suspend fun apply(player: PlaybackPort, nativeScope: PlaybackScope, authorizedHandle: String): PlaybackObservation {
        currentCoroutineContext().ensureActive()
        when (val action = operation) {
            is Operation.Pause -> player.setPaused(action.paused, nativeScope)
            is Operation.Seek -> player.seek(action.positionMs, nativeScope)
            is Operation.Rate -> player.setRate(action.rate, nativeScope)
            is Operation.KeepLoaded -> if (action.handle != authorizedHandle) throw WatchFailure("unauthorizedHandle")
        } // The selection owner already loaded this file; never reopen or rewind.
        val deadline = System.nanoTime() + 2_000_000_000L
        while (true) {
            currentCoroutineContext().ensureActive()
            val observation = player.observe(nativeScope)
            nativeScope.check(observation.scope)
            if (observation.status == PlaybackStatus.ready) {
                val reachedTarget = when (val action = operation) {
                    is Operation.Pause -> observation.paused == action.paused
                    is Operation.Seek -> abs(observation.positionMs - action.positionMs) <= 250
                    is Operation.Rate, is Operation.KeepLoaded -> true
                }
                if (reachedTarget) return observation
            } else if (observation.status != PlaybackStatus.loading || (operation !is Operation.Pause && operation !is Operation.Seek)) {
                throw WatchFailure("unavailable")
            }
            if (System.nanoTime() - deadline >= 0) throw WatchFailure("unavailable")
            delay(20)
        }
    }

    fun result(observation: PlaybackObservation, nativeScope: PlaybackScope): ByteArray {
        validate(observation, nativeScope)
        return reply("player.result", mapOf("status" to "applied", "positionMs" to observation.positionMs, "paused" to observation.paused))
    }
    /** Send only after the newer actual user frame has been published successfully. */
    fun cancelledReply() = reply("bridge.error", mapOf("code" to "cancelled", "message" to "cancelled", "retryable" to false))
    private fun reply(type: String, payload: Map<String, Any>): ByteArray = (Json.write(mapOf(
        "v" to 1L, "replyTo" to id, "type" to type, "clientRunId" to wireScope.clientRunId,
        "mediaGeneration" to wireScope.mediaGeneration, "payload" to payload,
    )) + "\n").toByteArray(Charsets.UTF_8)

    private fun validate(value: PlaybackObservation, nativeScope: PlaybackScope) {
        nativeScope.check(value.scope)
        if (value.status != PlaybackStatus.ready) throw WatchFailure("unavailable")
    }
    companion object {
        fun decode(frame: ByteArray, wireScope: PlaybackScope): PlayerCorrection {
            val value = frameValue(frame)
            keys(value, setOf("v", "id", "type", "clientRunId", "mediaGeneration", "payload"), setOf("afterUserAction"))
            if (integer(value["v"], 1) != 1L) bad()
            wireScope.check(PlaybackScope(text(value, "clientRunId"), integer(value["mediaGeneration"], PlaybackScope.MAXIMUM_GENERATION)))
            val id = text(value, "id")
            if (!id.matches(Regex("cmd-[1-9][0-9]{0,15}"))) bad()
            val after = if (value.containsKey("afterUserAction")) integer(value["afterUserAction"], PlaybackScope.MAXIMUM_GENERATION) else 0
            val payload = Json.`object`(value["payload"])
            val operation = when (text(value, "type")) {
                "player.setPaused" -> { keys(payload, setOf("paused")); Operation.Pause(payload["paused"] as? Boolean ?: bad()) }
                "player.setPosition" -> { keys(payload, setOf("positionMs")); Operation.Seek(integer(payload["positionMs"], 604800000)) }
                "player.setSpeed" -> {
                    keys(payload, setOf("rate")); val rate = number(payload["rate"])
                    if (rate !in 0.25..4.0) bad()
                    Operation.Rate(rate)
                }
                "player.openFile" -> {
                    keys(payload, setOf("mediaHandle")); val handle = text(payload, "mediaHandle")
                    PlaybackScope.validateId(handle); Operation.KeepLoaded(handle)
                }
                else -> bad()
            }
            return PlayerCorrection(id, after, operation, wireScope)
        }
        private fun bad(): Nothing = throw WatchFailure("malformed")
        internal fun text(value: Map<String, Any?>, key: String): String = value[key] as? String ?: bad()
        private fun number(value: Any?): Double {
            val number = (value as? Number)?.toDouble() ?: bad()
            if (!number.isFinite()) bad()
            return number
        }
        internal fun integer(value: Any?, maximum: Long): Long {
            val number = number(value)
            if (number < 0 || number > maximum.toDouble() || kotlin.math.floor(number) != number) bad()
            return number.toLong()
        }
        internal fun keys(value: Map<String, Any?>, required: Set<String>, optional: Set<String> = emptySet()) {
            if (!value.keys.containsAll(required) || !(required + optional).containsAll(value.keys)) bad()
        }
        internal fun frameValue(frame: ByteArray): Map<String, Any?> {
            if (frame.size !in 2..65536 || frame.last() != 10.toByte() || frame.dropLast(1).contains(10.toByte()) || frame.contains(13.toByte())) bad()
            val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(frame)).toString()
            return Json.`object`(Json.parse(text))
        }
    }
}
