package syncwatch.room

import java.nio.file.Files
import java.nio.file.Path

fun roomCallChecks(file: Path) {
    val fixture = RoomWire.parse(Files.readAllBytes(file))
    for (entry in fixture["statuses"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { RoomCallStatus.decode(RoomWire.string(item["wire"]).toByteArray(Charsets.UTF_8)) }
        check(result.isSuccess == item["valid"]) { "Room call corpus: ${item["name"]}" }
        if (result.isFailure) check(result.exceptionOrNull() is RoomProtocolException)
    }
    println("SYNCWATCH_ROOM_CALL_OK")
}
