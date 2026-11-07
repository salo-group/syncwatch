package syncwatch.room

import java.nio.file.Files
import java.nio.file.Path

fun meetAuthorizationChecks(file: Path) {
    val fixture = RoomWire.parse(Files.readAllBytes(file))
    val account = RoomWire.string(fixture["accountId"])
    for (entry in fixture["statuses"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { RoomMeetAuthorizationStatus.decode(RoomWire.string(item["wire"]).toByteArray(Charsets.UTF_8), account) }
        check(result.isSuccess == item["valid"]) { "Meet authorization corpus: ${item["name"]}" }
        if (result.isFailure) check(result.exceptionOrNull() is RoomProtocolException)
    }
    println("SYNCWATCH_MEET_AUTHORIZATION_OK")
}
