package syncwatch.room

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext

suspend fun accountSessionChecks(fixture: Path) {
    val source = RoomWire.parse(Files.readAllBytes(fixture))
    var valid: AccountSavedSession? = null
    for (entry in source["cases"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { AccountSavedSession.decode((item["wire"] as String).toByteArray(Charsets.UTF_8)) }
        check(result.isSuccess == item["valid"]) { "Account checkpoint: ${item["name"]}" }
        result.getOrNull()?.let { if (valid == null) valid = it; check(!it.toString().contains("B".repeat(43))) }
    }
    println("SYNCWATCH_ACCOUNT_RECORDS_OK")
    val helper = System.getenv("SYNCWATCH_ACCOUNT_VAULT_HELPER")?.let(Path::of) ?: return
    val directory = Files.createTempDirectory("sw-account-session-")
    val first = AccountSessionStore(directory, helper); val second = AccountSessionStore(directory, helper)
    try {
        val expected = requireNotNull(valid)
        check(first.read() == null); first.save(expected)
        check(runCatching { second.read() }.isFailure)
        val child = ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(), "-Xms16m", "-Xmx64m", "-cp",
            System.getProperty("java.class.path"), "syncwatch.room.AccountProfileLeaseProbe", directory.resolve("account-session.lock").toString())
            .redirectError(ProcessBuilder.Redirect.DISCARD).start()
        try {
            child.outputStream.close()
            check(child.waitFor(10, java.util.concurrent.TimeUnit.SECONDS))
            check(child.exitValue() == 0 && child.inputStream.readNBytes(128).toString(Charsets.UTF_8).trim() == "PROFILE_LEASE_HELD")
        } finally { if (child.isAlive) child.destroyForcibly(); child.waitFor(); child.inputStream.close(); child.errorStream.close() }
        first.close(); second.close()
        val restored = AccountSessionStore(directory, helper)
        try { check(restored.read()?.service == expected.service); restored.forget(); check(restored.read() == null) }
        finally { restored.close() }
        println("SYNCWATCH_ACCOUNT_PROFILE_RESTART_OK")
    } finally {
        withContext(NonCancellable) {
            first.close(); second.close()
            val cleanup = AccountSessionStore(directory, helper)
            try { cleanup.forget() } finally { cleanup.close() }
            Files.deleteIfExists(directory.resolve("account-session.lock")); Files.delete(directory)
        }
    }
}

object AccountProfileLeaseProbe {
    @JvmStatic fun main(args: Array<String>) {
        java.nio.channels.FileChannel.open(Path.of(args[0]), java.nio.file.StandardOpenOption.WRITE).use { channel ->
            val lock = channel.tryLock()
            if (lock == null) println("PROFILE_LEASE_HELD") else { lock.release(); error("Profile lease was released by the rejected second owner") }
        }
    }
}
