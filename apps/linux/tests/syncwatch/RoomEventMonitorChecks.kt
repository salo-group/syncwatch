package syncwatch

import java.util.UUID
import kotlin.time.Duration.Companion.seconds
import kotlin.time.TimeMark
import kotlin.time.TimeSource
import kotlinx.coroutines.*
import syncwatch.domain.MediaDescriptor
import syncwatch.room.*

internal suspend fun roomEventMonitorChecks(address: String, key: String) = coroutineScope {
    val client = RoomClient(RoomAddress(address))
    var monitoring: Job? = null
    var publishing: Deferred<RoomCatalogView>? = null
    try {
        val identity = RoomIdentity("run-" + UUID.randomUUID(), "Linux recovery")
        val admission = client.create(identity, RoomConnection("syncplay.example.com", 8999, "Recovery"), RoomSecret(key))
        val owner = RoomMembershipSession(client, admission, identity)
        val media = MediaDescriptor("movie", "Movie", "movie", 10, 60000, null)
        owner.publish(listOf(media))
        val corpus = (0 until 120).map { MediaDescriptor("extra-$it", "Extra", "extra", 10, 60000, null) }
        publishing = async { owner.publish(listOf(media) + corpus) }
        var outageStarted: TimeMark? = null
        var deliveries = 0
        var recoveries = 0
        val recovered = CompletableDeferred<Unit>()
        monitoring = launch {
            RoomEventMonitor.run(RoomAddress(address), owner, reconnecting = { recoveries++ }, accepted = { view ->
                check(view.bootstrap.snapshot.members.any { it.memberId == admission.memberId && it.identity == identity })
                val start = outageStarted ?: TimeSource.Monotonic.markNow().also { outageStarted = it }
                if (++deliveries <= 6 || start.elapsedNow() < 20.seconds) throw RoomProtocolException("transport")
                recovered.complete(Unit)
            })
        }
        publishing.await(); publishing = null
        withTimeout(28000) { recovered.await() }
        check(recoveries > 0)
        owner.publish(listOf(media))
        val selected = owner.perform(RoomCommandAction.Select(media.mediaId), owner.watchContext())
        check(selected.definition.selection?.media == media)
        monitoring.cancelAndJoin(); monitoring = null
        var exhaustedAttempts = 0; var exhaustedRetries = 0
        val exhausted = runCatching {
            withTimeout(28000) {
                RoomEventMonitor.run(RoomAddress(address), owner, reconnecting = { exhaustedRetries++ },
                    accepted = { exhaustedAttempts++; throw RoomProtocolException("transport") })
            }
        }.exceptionOrNull()
        check(exhausted is RoomProtocolException && exhausted.code == "transport" &&
            exhaustedRetries in 1..8 && exhaustedAttempts <= exhaustedRetries + 1)
        var retries = 0
        val failure = runCatching {
            RoomEventMonitor.run(RoomAddress(address), owner, reconnecting = { retries++ },
                accepted = { throw RoomProtocolException("malformed") })
        }.exceptionOrNull()
        check(failure is RoomProtocolException && failure.code == "malformed" && retries == 0)
        client.leave(admission)
        println("SYNCWATCH_ROOM_EVENT_RECOVERY_OK retained identity/commands, terminal metadata, cancel/join")
    } finally {
        withContext(NonCancellable) { publishing?.cancelAndJoin(); monitoring?.cancelAndJoin(); client.close() }
    }
}
