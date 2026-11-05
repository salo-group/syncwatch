package syncwatch.room

import java.util.UUID
import kotlinx.coroutines.*

suspend fun roomAccountPortChecks(fixture: Map<String, Any?>) = supervisorScope {
    val bootstrap = RoomWire.parse(RoomWire.string(fixture["catalogBootstrap"]).toByteArray(Charsets.UTF_8))
    val payload = mapOf("roomId" to "room-test", "memberId" to "member-owner", "expiresAt" to "2030-01-01T00:00:00Z", "bootstrap" to bootstrap)
    val bytes = RoomWire.encode(payload); val scope = UUID.randomUUID()
    val admitted = RoomAccessWire.admission(bytes, "run-room-client", scope)
    check(admitted.token == null && admitted.invitation == null && admitted.isAccountBacked && admitted.accountScope == scope)
    fun reject(action: () -> Unit) {
        try { action(); error("Invalid account admission accepted") } catch (error: RoomProtocolException) { check(error.code == "malformed") }
    }
    reject { RoomAccessWire.admission(bytes, "other-run", scope) }
    reject { RoomAccessWire.admission(bytes, "run-room-client", scope, "other-room") }
    reject { RoomAccessWire.admission(RoomWire.encode(payload + ("token" to "A".repeat(64))), "run-room-client", scope) }
    reject { RoomAccessWire.result(RoomWire.encode(mapOf("v" to 1L, "result" to payload))) }
    reject { RoomAccessWire.result("{\"v\":2,\"v\":2,\"result\":{}}".toByteArray()) }
    check((RoomWire.value(RoomAccessWire.result(RoomWire.encode(mapOf("v" to 2L, "result" to emptyList<Any>())))) as List<*>).isEmpty())
    val address = RoomAddress("http://127.0.0.1:1/"); val owner = UUID.randomUUID()
    val device = AccountDeviceSession(AccountProfile("A".repeat(43), "Owner", "owner@example.invalid"), "B".repeat(43), "R".repeat(43), address.origin, owner)
    val authority = RoomAccountAuthority(address, device, owner); val client = RoomClient(authority)
    try {
        try { client.snapshot(admitted); error("Foreign authority reached transport") } catch (error: RoomProtocolException) { check(error.code == "malformed") }
        val admission = RoomAccessWire.admission(bytes, "run-room-client", authority.scope)
        authority.pause()
        val request = async { client.create("run-new", "create-new") }
        val events = async { client.openEvents(admission) }
        withTimeout(3_000) { while (authority.pendingRequests != 2) yield() }
        client.close()
        for (work in listOf(request, events)) {
            try { work.await(); error("Closed client completed paused authorization") } catch (_: CancellationException) { }
        }
        check(authority.pendingRequests == 0)
        authority.commit(device); check(authority.credential().accountId == device.account.id)
        println("SYNCWATCH_ROOM_ACCOUNT_PORT_OK")
    } finally { client.close(); authority.close() }
}
