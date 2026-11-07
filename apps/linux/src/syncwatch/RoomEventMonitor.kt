package syncwatch

import kotlinx.coroutines.*
import syncwatch.room.*

/** Replaces only the event transport; membership commands retain their owner and numbering. */
internal object RoomEventMonitor {
    suspend fun run(address: RoomAddress, membership: RoomMembershipSession, authority: RoomAccountAuthority? = null,
        reconnecting: () -> Unit, accepted: (RoomCatalogView) -> Unit) {
        val delays = longArrayOf(500, 1000, 2000, 4000, 8000)
        var failures = 0
        var deadline: Long? = null
        while (true) {
            currentCoroutineContext().ensureActive()
            val client = authority?.let(::RoomClient) ?: RoomClient(address)
            val failure: Exception
            try {
                val allowance = minOf(20000L, deadline?.let { (it - System.nanoTime()) / 1000000 } ?: 20000L)
                if (allowance <= 0) throw RoomProtocolException("transport")
                val (stream, first) = withTimeout(allowance) {
                    membership.connectEvents(client, recovering = failures > 0)
                }
                currentCoroutineContext().ensureActive()
                accepted(first); failures = 0; deadline = null
                var previous = first.bootstrap.snapshot
                while (true) {
                    val event = RoomInvalidation.decode(stream.receive())
                    if (event.epoch != previous.epoch) throw RoomProtocolException("malformed")
                    if (event.sequence <= previous.sequence) continue
                    val view = membership.refresh(sequence = event.sequence)
                    currentCoroutineContext().ensureActive(); accepted(view); previous = view.bootstrap.snapshot
                }
            } catch (error: Exception) { failure = error }
            finally { client.close() }
            currentCoroutineContext().ensureActive()
            if (!RoomController.isMetadataInterruption(failure) || failures >= delays.size) throw failure
            val until = deadline ?: (System.nanoTime() + 25000000000L).also { deadline = it }
            if (System.nanoTime() + delays[failures] * 1000000 >= until) throw failure
            reconnecting(); delay(delays[failures]); failures++
        }
    }
}
