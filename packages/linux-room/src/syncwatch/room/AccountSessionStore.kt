package syncwatch.room

import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.file.*
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class AccountSavedSession internal constructor(val service: String, internal val accountId: String,
    internal val refreshToken: String, internal val requestId: String) {
    init {
        if (service.toByteArray(Charsets.UTF_8).size > 256) throw RoomWire.bad()
        RoomAddress(service)
        for (value in listOf(accountId, refreshToken, requestId)) if (!value.matches(Regex("[A-Za-z0-9_-]{43}"))) throw RoomWire.bad()
    }
    override fun toString() = "[saved account redacted]"
    internal fun encode(): ByteArray = RoomWire.encode(mapOf("v" to 1L, "service" to service, "accountId" to accountId,
        "refreshToken" to refreshToken, "requestId" to requestId))
    companion object {
        fun decode(bytes: ByteArray): AccountSavedSession {
            if (bytes.size !in 1..2048) throw RoomWire.bad()
            val value = RoomWire.parse(bytes); RoomWire.keys(value, "v", "service", "accountId", "refreshToken", "requestId")
            if (value["v"] != 1L) throw RoomWire.bad()
            return AccountSavedSession(RoomWire.string(value["service"]), RoomWire.string(value["accountId"]),
                RoomWire.string(value["refreshToken"]), RoomWire.string(value["requestId"]))
        }
    }
}

/** One profile owns its OS record. The file lock contains no credentials or profile data. */
class AccountSessionStore(private val directory: Path, private val helper: Path) {
    private val gate = Mutex()
    private var lease: FileLock? = null
    private var channel: FileChannel? = null
    private var storage: AccountCredentialVault? = null
    private var ownedDirectory: Path? = null
    private var closed = false
    private fun vault(): AccountCredentialVault {
        if (closed) throw AccountVaultException()
        storage?.let { return it }
        Files.createDirectories(directory, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
        val canonical = directory.toRealPath()
        // Closing any descriptor of a POSIX record-locked file can drop this process's lock.
        // Refuse another owner before opening a second descriptor in this JVM.
        synchronized(owners) { if (!owners.add(canonical)) throw AccountVaultException() }
        var opened: FileChannel? = null
        try {
            opened = FileChannel.open(canonical.resolve("account-session.lock"),
                setOf<OpenOption>(StandardOpenOption.CREATE, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS),
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
            val lock = opened.tryLock() ?: throw AccountVaultException()
            val scope = MessageDigest.getInstance("SHA-256").digest(canonical.toString().toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            return AccountCredentialVault(helper, scope).also { storage = it; channel = opened; lease = lock; ownedDirectory = canonical }
        } catch (error: Exception) { try { opened?.close() } finally { synchronized(owners) { owners.remove(canonical) } }; throw error }
    }
    suspend fun read(): AccountSavedSession? = withContext(Dispatchers.IO) { gate.withLock { vault().read()?.let(AccountSavedSession::decode) } }
    suspend fun save(record: AccountSavedSession) = withContext(Dispatchers.IO) { gate.withLock { vault().write(record.encode()) } }
    suspend fun forget() = withContext(Dispatchers.IO) { gate.withLock { vault().remove() } }
    suspend fun close() = withContext(Dispatchers.IO) {
        gate.withLock {
            closed = true; storage = null
            try { lease?.release() }
            finally {
                try { channel?.close() }
                finally { ownedDirectory?.let { synchronized(owners) { owners.remove(it) } }; ownedDirectory = null; lease = null; channel = null }
            }
        }
    }
    companion object { private val owners = HashSet<Path>() }
}
