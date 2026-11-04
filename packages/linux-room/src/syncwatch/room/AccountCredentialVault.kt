package syncwatch.room

import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runInterruptible

class AccountVaultException(operation: String = "", exit: Int? = null) : Exception(
    "Account credential storage unavailable" + (if (operation.isEmpty()) "" else " ($operation, exit $exit)"))

/** Secret Service is accessed by the owned, bounded native helper; secrets travel only through private pipes. */
class AccountCredentialVault(private val helper: Path, private val scope: String) {
    init { require(scope.matches(Regex("[0-9a-f]{64}"))) { "Invalid account storage scope" } }
    suspend fun read(): ByteArray? = invoke("read", null)
    suspend fun write(data: ByteArray) {
        require(data.size in 1..2048) { "Invalid account storage record" }
        invoke("write", data)
    }
    suspend fun remove() { invoke("remove", null) }
    private suspend fun invoke(operation: String, data: ByteArray?): ByteArray? = runInterruptible(Dispatchers.IO) {
        if (!helper.isAbsolute || !Files.isRegularFile(helper, LinkOption.NOFOLLOW_LINKS) || !Files.isExecutable(helper)) throw AccountVaultException()
        val builder = ProcessBuilder(helper.toString(), operation, scope).redirectError(ProcessBuilder.Redirect.DISCARD)
        builder.environment().keys.removeAll { it.startsWith("LD_") }
        val process = try { builder.start() } catch (_: java.io.IOException) { throw AccountVaultException() }
        try {
            process.outputStream.use { if (data != null) it.write(data) }
            if (!process.waitFor(15, TimeUnit.SECONDS)) throw AccountVaultException()
            val output = process.inputStream.use { it.readNBytes(2049) }
            if (operation == "read" && process.exitValue() == 2 && output.isEmpty()) return@runInterruptible null
            if (process.exitValue() != 0 || (operation == "read" && output.size !in 1..2048) ||
                (operation != "read" && output.isNotEmpty())) throw AccountVaultException(operation, process.exitValue())
            if (operation == "read") output else null
        } catch (_: java.io.IOException) {
            throw AccountVaultException(operation)
        } finally {
            if (process.isAlive) process.destroyForcibly()
            // Reap even after interruption; the helper never owns a child process.
            var interrupted = false
            while (process.isAlive) try { process.waitFor() } catch (_: InterruptedException) { interrupted = true }
            runCatching { process.inputStream.close() }; runCatching { process.errorStream.close() }; runCatching { process.outputStream.close() }
            if (interrupted) Thread.currentThread().interrupt()
        }
    }
}
