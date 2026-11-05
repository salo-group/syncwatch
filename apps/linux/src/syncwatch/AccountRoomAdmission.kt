package syncwatch

import kotlinx.coroutines.*
import syncwatch.room.*
import java.util.UUID

internal class RoomAdmissionFailure(val notice: String) : Exception()

/** A correlated admission owns its retries and retains cleanup authority until the caller accepts it. */
internal class AccountRoomAdmission(private val client: RoomClient, private val owner: Boolean, values: Map<String, String>) {
    private val invitation = if (owner) null else RoomInvitation(values.getValue("roomID"), RoomSecret.fromInput(values.getValue("invitation")))
    private val run = "run-${UUID.randomUUID()}"
    private val requestId = "request-${UUID.randomUUID()}"
    private var attempt: RoomAdmissionAttempt? = null
    suspend fun perform(waiting: () -> Unit): RoomAdmission = withTimeoutOrNull(120_000) {
        if (owner) return@withTimeoutOrNull retry { client.create(run, requestId) }
        val request = retry { client.requestAdmission(requireNotNull(invitation), run, requestId) }
        attempt = request; currentCoroutineContext().ensureActive(); waiting()
        var status = request.request.status
        while (true) {
            if (status == "approved" || status == "admitted") return@withTimeoutOrNull retry { client.accept(request) }
            if (status == "denied") throw RoomAdmissionFailure("roomAdmissionDenied")
            if (status != "pending") throw RoomAdmissionFailure("roomAdmissionExpired")
            delay(3_000); status = retry { client.admissionStatus(request) }.status
        }
        @Suppress("UNREACHABLE_CODE") error("Unreachable admission")
    } ?: throw RoomAdmissionFailure("roomAdmissionExpired")
    suspend fun cancel() { attempt?.let { client.cancelAdmission(it) } }
    private suspend fun <T> retry(operation: suspend () -> T): T {
        for (index in 0..1) {
            try { return operation() } catch (error: Exception) {
                currentCoroutineContext().ensureActive()
                val cause = if (error is java.util.concurrent.CompletionException) error.cause ?: error else error
                val transient = cause is RoomProtocolException && cause.code == "transport" || cause is TimeoutCancellationException ||
                    cause is java.io.IOException && cause !is javax.net.ssl.SSLException
                if (index != 0 || !transient) throw error
                delay(1_000)
            }
        }
        throw RoomProtocolException("transport")
    }
}
