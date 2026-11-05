package syncwatch.room

import java.net.URI
import java.util.UUID
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

internal class AccountRoomCredential(val origin: URI, val owner: UUID, val accountId: String, val access: String) {
    override fun toString() = "[room account credential redacted]"
    companion object {
        fun from(session: AccountDeviceSession): AccountRoomCredential = AccountRoomCredential(
            session.issuer ?: throw RoomProtocolException("malformed"),
            session.credentialOwner ?: throw RoomProtocolException("malformed"), session.account.id, session.access)
    }
}

/** Borrowed account authority; rotation is published only after the secure checkpoint. */
class RoomAccountAuthority internal constructor(val address: RoomAddress, session: AccountDeviceSession, private val owner: UUID) : AutoCloseable {
    val accountId = session.account.id
    internal val scope = UUID.randomUUID()
    private val gate = Any()
    private var current: AccountRoomCredential? = AccountRoomCredential.from(session)
    private var pending: CompletableDeferred<AccountRoomCredential>? = null
    private var paused = false
    private var closed = false
    private var waiters = 0
    internal val pendingRequests: Int get() = synchronized(gate) { waiters }
    init {
        val initial = requireNotNull(current)
        if (initial.origin != address.origin || initial.owner != owner) throw RoomProtocolException("malformed")
    }
    fun pause() = synchronized(gate) { needOpen(); current = null; paused = true }
    fun commit(session: AccountDeviceSession) {
        val credential = AccountRoomCredential.from(session)
        val wake = synchronized(gate) {
            needOpen()
            if (credential.origin != address.origin || credential.owner != owner || credential.accountId != accountId) throw RoomProtocolException("malformed")
            current = credential; paused = false
            pending.also { pending = null }
        }
        wake?.complete(credential)
    }
    fun unavailable() { block(false) }
    override fun close() { block(true) }
    private fun block(close: Boolean) {
        val wake = synchronized(gate) {
            if (closed) return
            current = null; paused = false; closed = close
            pending.also { pending = null }
        }
        wake?.completeExceptionally(RoomProtocolException(if (close) "closed" else "transport"))
    }
    internal suspend fun credential(): AccountRoomCredential {
        currentCoroutineContext().ensureActive()
        val task = synchronized(gate) {
            needOpen()
            current?.let { return it }
            if (!paused) throw RoomProtocolException("transport")
            if (waiters >= 64) throw RoomProtocolException("queueFull")
            waiters++
            pending ?: CompletableDeferred<AccountRoomCredential>().also { pending = it }
        }
        try {
            val credential = task.await()
            currentCoroutineContext().ensureActive()
            return credential
        } finally { synchronized(gate) { waiters-- } }
    }
    private fun needOpen() { if (closed) throw RoomProtocolException("closed") }
}
