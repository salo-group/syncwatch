package syncwatch

import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import syncwatch.domain.MediaDescriptor
import syncwatch.room.*

internal data class RoomWatchContext(val bootstrap: RoomBootstrap, val definition: RoomSelectionDefinition)

/** One application-owned membership serializes bootstrap walks and command numbering. */
internal class RoomMembershipSession(private val client: RoomClient, private val admission: RoomAdmission, private val identity: RoomIdentity) {
    suspend fun invitation(): RoomInvitation = client.invite(admission)
    suspend fun pendingAdmissions(): List<RoomAdmissionRequest> = client.pending(admission)
    suspend fun decideAdmission(id: String, approve: Boolean) { client.decide(admission, id, approve) }
    suspend fun syncTicket(): RoomSyncTicket = client.syncTicket(admission)
    private val gate = Mutex()
    private var minimumSequence = admission.bootstrap.snapshot.sequence
    private var number = 0L
    private var unresolved: RoomCommand? = null

    suspend fun refresh(initial: RoomBootstrap? = null, sequence: Long = 0): RoomCatalogView = gate.withLock {
        minimumSequence = maxOf(minimumSequence, sequence)
        read(initial)
    }
    // A socket bootstrap replaces the server cursor, so it shares the command/catalog lock.
    suspend fun connectEvents(events: RoomClient, recovering: Boolean): Pair<RoomEventStream, RoomCatalogView> = gate.withLock {
        if (recovering) validatedSnapshot()
        val stream = events.openEvents(admission)
        RoomBootstrap.initialEvent(stream.receive(), admission)
        stream to read(null)
    }
    suspend fun publish(items: List<MediaDescriptor>): RoomCatalogView = gate.withLock {
        if (items.size > 2048 || items.map { it.mediaId }.toSet().size != items.size) throw RoomProtocolException("malformed")
        if (unresolved != null) acknowledge()
        for (attempt in 0 until 2) {
            val snapshot = validatedSnapshot()
            val batch = "batch-" + java.util.UUID.randomUUID()
            var offset = 0
            try {
                do {
                    val command = catalogCommand(admission, number + 1, snapshot.snapshot.watchGeneration, batch, items, offset)
                    number++; unresolved = command.first
                    acknowledge(); offset += command.second
                } while (offset < items.size)
                return@withLock read(null)
            } catch (error: RoomProtocolException) {
                if (error.code != "staleGeneration" || attempt != 0) throw error
            }
        }
        throw RoomProtocolException("staleGeneration")
    }
    suspend fun watchContext(): RoomWatchContext = gate.withLock {
        if (unresolved != null) acknowledge()
        readWatchContext()
    }
    /** A peer generation change requires an explicit new user decision. */
    suspend fun perform(action: RoomCommandAction, expected: RoomWatchContext): RoomWatchContext = gate.withLock {
        if (unresolved != null) acknowledge()
        val current = validatedSnapshot()
        if (current.snapshot.epoch != expected.bootstrap.snapshot.epoch || current.snapshot.watchGeneration != expected.bootstrap.snapshot.watchGeneration)
            throw RoomProtocolException("staleGeneration")
        val command = RoomCommand(admission, number + 1, current.snapshot.watchGeneration, action)
        number++; unresolved = command
        acknowledge()
        readWatchContext()
    }
    private suspend fun readWatchContext(): RoomWatchContext {
        for (attempt in 0 until 2) {
            val bootstrap = validatedSnapshot()
            try {
                val definition = client.selection(admission, bootstrap)
                currentCoroutineContext().ensureActive(); minimumSequence = maxOf(minimumSequence, definition.sequence)
                return RoomWatchContext(bootstrap, definition)
            } catch (error: RoomProtocolException) {
                if (error.code != "staleGeneration" || attempt != 0) throw error
            }
        }
        throw RoomProtocolException("staleGeneration")
    }
    private suspend fun acknowledge() {
        val command = unresolved ?: return
        val result = client.execute(admission, command)
        unresolved = null; minimumSequence = maxOf(minimumSequence, result.sequence)
        result.errorCode?.let { throw RoomProtocolException(it) }
        currentCoroutineContext().ensureActive()
    }
    private suspend fun validatedSnapshot(initial: RoomBootstrap? = null): RoomBootstrap {
        val value = initial ?: client.snapshot(admission)
        val expected = admission.bootstrap.connection
        if (value.snapshot.epoch != admission.bootstrap.snapshot.epoch || value.snapshot.sequence < minimumSequence ||
            value.connection.host != expected.host || value.connection.port != expected.port || value.connection.room != expected.room ||
            value.snapshot.members.none { it.memberId == admission.memberId && it.identity == identity }) throw RoomProtocolException("malformed")
        return value
    }
    private suspend fun read(initial: RoomBootstrap?): RoomCatalogView {
        val bootstrap = validatedSnapshot(initial)
        val view = withTimeout(20000) { client.catalogView(admission, bootstrap) }
        currentCoroutineContext().ensureActive(); minimumSequence = view.bootstrap.snapshot.sequence
        return view
    }
    companion object {
        fun catalogCommand(admission: RoomAdmission, number: Long, generation: Long, batch: String, items: List<MediaDescriptor>, offset: Int): Pair<RoomCommand, Int> {
            if (offset !in 0..items.size) throw RoomProtocolException("malformed")
            var count = minOf(100, items.size - offset)
            while (true) {
                try { return RoomCommand(admission, number, generation, RoomCommandAction.Catalog(batch, offset.toLong(), items.subList(offset, offset + count), offset + count == items.size)) to count }
                catch (error: RoomProtocolException) {
                    if (error.code != "frameTooLarge" || count <= 1) throw error
                    count /= 2
                }
            }
        }
    }
}
