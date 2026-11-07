package syncwatch.bridge.probe

import java.io.EOFException
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.CancellationException
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import syncwatch.bridge.HelperProcess
import syncwatch.ipc.Json

/** Real inherited-pipe acceptance, independent of the session/command model tests. */
fun main(arguments: Array<String>) {
    check(System.getProperty("os.name") == "Linux") { "Kotlin bridge probe requires Linux" }
    require(arguments.size == 2)
    val executable = Path.of(arguments[0]); val runtime = Path.of(arguments[1])
    val revision = Files.readString(runtime.resolve("app/syncplay-revision.txt")).trim()
    check(revision.matches(Regex("[0-9a-f]{40}")))
    fun frame(id: Long, type: String, payload: Map<String, Any?>) =
        (Json.write(mapOf("v" to 1L, "id" to "cmd-$id", "type" to type,
            "clientRunId" to "native-probe", "mediaGeneration" to 2L, "payload" to payload)) + "\n").toByteArray(Charsets.UTF_8)
    fun read(helper: HelperProcess, id: Long, type: String): Map<String, Any?> {
        val response = Json.`object`(Json.parse(String(helper.read().get(15, TimeUnit.SECONDS), Charsets.UTF_8)))
        check(response.keys == setOf("v", "replyTo", "type", "clientRunId", "mediaGeneration", "payload"))
        check(response["v"] == 1L && response["replyTo"] == "cmd-$id" && response["type"] == type)
        check(response["clientRunId"] == "native-probe" && response["mediaGeneration"] == 2L)
        return Json.`object`(response["payload"])
    }
    fun owned(operation: (HelperProcess) -> Unit) {
        val helper = HelperProcess.start(executable, runtime)
        try { operation(helper) }
        finally {
            helper.close(); helper.close()
            check(!ProcessHandle.of(helper.processID).map { it.isAlive }.orElse(false)) { "Owned helper remains alive" }
        }
    }
    owned { helper ->
        val hello = frame(1, "bridge.hello", mapOf("supportedVersions" to listOf(1L)))
        helper.send(hello).get(5, TimeUnit.SECONDS)
        val ready = read(helper, 1, "bridge.ready")
        check(ready == mapOf("syncplayRevision" to revision, "admissionVersion" to 1L)) {
            "Current helper must advertise its pinned revision and admission capability"
        }
        helper.send(hello).get(5, TimeUnit.SECONDS)
        check(read(helper, 1, "bridge.ready") == ready)
        helper.send(frame(2, "player.loaded", mapOf("mediaHandle" to "media-1", "name" to "Movie.mkv",
            "durationMs" to 60_000L, "fileSizeBytes" to 1234L))).get(5, TimeUnit.SECONDS)
        check(read(helper, 2, "bridge.result") == mapOf("status" to "applied"))
        helper.send(frame(3, "bridge.shutdown", emptyMap())).get(5, TimeUnit.SECONDS)
        check(read(helper, 3, "bridge.stopped").isEmpty())
    }
    owned { helper ->
        val pending = helper.read()
        check(pending.cancel(true))
        try { pending.get(5, TimeUnit.SECONDS); error("Cancellation was ignored") }
        catch (_: CancellationException) { }
    }
    owned { helper ->
        check(ProcessHandle.of(helper.processID).orElseThrow().destroyForcibly())
        try { helper.read().get(5, TimeUnit.SECONDS); error("Crash was ignored") }
        catch (error: ExecutionException) { check(error.cause is EOFException) }
    }
    println("SYNCWATCH_NATIVE_BRIDGE_OK handshake replay media shutdown cancellation crash")
}
