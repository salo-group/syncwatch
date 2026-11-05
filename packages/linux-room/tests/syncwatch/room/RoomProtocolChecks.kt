package syncwatch.room

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.ipc.Json

fun main(args: Array<String>) {
    val fixture = RoomWire.obj(Json.parse(Files.readString(Path.of(args[0]))))
    val identity = RoomIdentity("run-room-client", "Room User")
    val bootstrap = RoomBootstrap.decode((fixture["catalogBootstrap"] as String).toByteArray(Charsets.UTF_8))
    for (entry in fixture["catalogs"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { RoomCatalogPage.decode((item["wire"] as String).toByteArray(Charsets.UTF_8), bootstrap, item["cursor"] as String) }
        check(result.isSuccess == item["valid"]) { "catalog: ${item["name"]}" }
        if (!result.isSuccess) check(result.exceptionOrNull() is RoomProtocolException)
        result.getOrNull()?.let {
            check(it.epoch == bootstrap.snapshot.epoch && it.sequence == bootstrap.snapshot.sequence &&
                it.items.size.toLong() == item["count"] && it.nextCursor == item["nextCursor"])
        }
    }
    for (entry in fixture["invitations"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { RoomInvitation.parse(item["text"] as String) }
        check(result.isSuccess == item["valid"]) { "invitation: ${item["name"]}" }
        result.getOrNull()?.let {
            val credential = item["credential"] as String
            check(it.roomId == item["roomID"] && it.copyText() == "${item["roomID"]}\n$credential" && !it.toString().contains(credential))
        }
    }
    for (entry in fixture["addresses"] as List<*>) {
        val item = RoomWire.obj(entry)
        val accepted = runCatching { RoomAddress(item["value"] as String) }.isSuccess
        check(accepted == item["valid"]) { "address: ${item["value"]}" }
    }
    for (entry in fixture["admissions"] as List<*>) {
        val item = RoomWire.obj(entry)
        val result = runCatching { RoomAdmission.decode((item["wire"] as String).toByteArray(Charsets.UTF_8), identity, item["owner"] as Boolean) }
        check(result.isSuccess == item["valid"]) { "admission: ${item["name"]}" }
        result.getOrNull()?.let {
            check(it.memberId == "member-owner" && it.bootstrap.connection.port == 8999)
            check(!it.toString().contains("A".repeat(64)) && !it.toString().contains("B".repeat(64)))
        }
    }
    println("SYNCWATCH_ROOM_PROTOCOL_OK")
    meetAuthorizationChecks(Path.of(args[0]).resolveSibling("meet-authorization.json"))
    roomCallChecks(Path.of(args[0]).resolveSibling("room-call.json"))
    selectionChecks(Path.of(args[0]).resolveSibling("room-selection.json"))
    kotlinx.coroutines.runBlocking { accountCredentialVaultChecks(); roomAccountAuthorityChecks(); roomAccountPortChecks(fixture); roomAccountTransportChecks(); accountSessionChecks(Path.of(args[0]).resolveSibling("account-persistence.json")); catalogViewChecks(fixture); commandChecks(fixture); transportChecks() }
}
