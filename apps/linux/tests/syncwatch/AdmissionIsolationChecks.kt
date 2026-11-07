package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.*
import syncwatch.room.*

internal suspend fun admissionIsolationChecks() = coroutineScope {
    val path = System.getenv("SYNCWATCH_ADMISSION_ISOLATION_FIXTURE") ?: return@coroutineScope
    val fixture = RoomWire.parse(Files.readAllBytes(Path.of(path)))
    val identityData = fixture["identity"] as Map<*, *>
    val identity = RoomIdentity(identityData["clientRunId"] as String, identityData["syncplayUsername"] as String)
    val admission = RoomAdmission.decode(RoomWire.encode(fixture["admission"]), identity, true)
    val address = RoomAddress(fixture["service"] as String)
    val client = RoomClient(address); val events = RoomClient(address)
    val owner = RoomMembershipSession(client, admission, identity)
    var connecting: Deferred<*>? = null; var reading: Deferred<RoomWatchContext>? = null
    try {
        connecting = async { owner.connectEvents(events, false) }
        withTimeout(3000) { while (!Files.exists(Path.of(fixture["held"] as String))) delay(10) }
        val task = async { owner.admissionContext(admission.bootstrap) }; reading = task
        val context = withTimeoutOrNull(2000) { task.await() }
        check(context != null) { "Admission waited for the held event bootstrap" }
        check(context.definition.watchGeneration == 2L && context.definition.sequence == 8L && context.definition.selection?.media?.mediaId == "movie")
        for (value in fixture["invalidAnchors"] as List<*>) {
            val anchor = RoomBootstrap.decode(RoomWire.encode(value))
            check((runCatching { owner.admissionContext(anchor) }.exceptionOrNull() as? RoomProtocolException)?.code == "malformed")
        }
        val advanced = RoomAdmission.decode(RoomWire.encode(fixture["advancedAdmission"]), identity, true)
        val newer = RoomMembershipSession(client, advanced, identity)
        check((runCatching { newer.admissionContext(admission.bootstrap) }.exceptionOrNull() as? RoomProtocolException)?.code == "malformed")
        check(!Files.exists(Path.of(fixture["unexpectedBootstrap"] as String)))
        println("SYNCWATCH_ADMISSION_ISOLATION_OK held event bootstrap, fresh edition, retained cursor")
    } finally {
        withContext(NonCancellable) {
            connecting?.cancel(); events.close(); connecting?.join()
            reading?.cancelAndJoin(); client.close()
        }
    }
}
