package syncwatch.room

import java.nio.file.Path
import java.util.UUID
import kotlinx.coroutines.*

suspend fun accountCredentialVaultChecks() {
    val absent = Path.of("/nonexistent/syncwatch-account-store")
    for (scope in listOf("", "a".repeat(63), "A".repeat(64), "a".repeat(64) + "\n")) {
        check(runCatching { AccountCredentialVault(absent, scope) }.exceptionOrNull() is IllegalArgumentException)
    }
    val invalid = AccountCredentialVault(absent, "a".repeat(64))
    for (data in listOf(byteArrayOf(), ByteArray(2049))) {
        check(runCatching { invalid.write(data) }.exceptionOrNull() is IllegalArgumentException)
    }
    check(runCatching { invalid.read() }.exceptionOrNull() is AccountVaultException)
    val work = java.nio.file.Files.createTempDirectory("sw-vault-process-checks-")
    try {
        val fake = work.resolve("store")
        fun script(text: String) {
            java.nio.file.Files.writeString(fake, "#!/bin/sh\n$text\n")
            java.nio.file.Files.setPosixFilePermissions(fake, java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"))
        }
        val failed = AccountCredentialVault(fake, "a".repeat(64))
        script("exit 3")
        check(runCatching { failed.read() }.exceptionOrNull() is AccountVaultException)
        script("printf unexpected")
        check(runCatching { failed.write(byteArrayOf(1)) }.exceptionOrNull() is AccountVaultException)
        val pid = work.resolve("pid")
        val quoted = "'" + pid.toString().replace("'", "'\\''") + "'"
        script("printf '%s' \"\$\$\" > $quoted\nexec /usr/bin/sleep 30")
        coroutineScope {
            val pending = launch { failed.read() }
            try { withTimeout(5000) { while (!java.nio.file.Files.exists(pid)) delay(10) } }
            finally { pending.cancelAndJoin() }
        }
        check(!ProcessHandle.of(java.nio.file.Files.readString(pid).toLong()).map { it.isAlive }.orElse(false))
        println("SYNCWATCH_ACCOUNT_VAULT_FAILURES_OK unavailable, unexpected output and cancelled child reaped")
    } finally {
        java.nio.file.Files.list(work).use { entries -> entries.forEach { java.nio.file.Files.delete(it) } }
        java.nio.file.Files.delete(work)
    }
    val helper = System.getenv("SYNCWATCH_ACCOUNT_VAULT_HELPER") ?: return
    fun scope() = (UUID.randomUUID().toString() + UUID.randomUUID().toString()).replace("-", "")
    val scopes = listOf(scope(), scope())
    System.getenv("SYNCWATCH_ACCOUNT_VAULT_SCOPES")?.let { java.nio.file.Files.write(Path.of(it), scopes) }
    val vault = AccountCredentialVault(Path.of(helper), scopes[0])
    val independent = AccountCredentialVault(Path.of(helper), scopes[1])
    var failure: Throwable? = null
    try {
        check(vault.read() == null)
        val first = byteArrayOf(0, 1, 2, -1); val second = ByteArray(2048) { 42 }
        vault.write(first)
        check(vault.read()?.contentEquals(first) == true && independent.read() == null)
        vault.write(second)
        check(vault.read()?.contentEquals(second) == true)
        vault.remove(); check(vault.read() == null); vault.remove()
        println("SYNCWATCH_ACCOUNT_VAULT_OS_OK")
    } catch (error: Throwable) { failure = error; throw error }
    finally {
        for (owned in listOf(vault, independent)) try { owned.remove() }
        catch (cleanup: Throwable) { if (failure == null) throw cleanup else failure.addSuppressed(cleanup) }
    }
}
