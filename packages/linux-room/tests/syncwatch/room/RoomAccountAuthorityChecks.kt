package syncwatch.room

import java.net.URI
import java.util.UUID
import kotlinx.coroutines.*

suspend fun roomAccountAuthorityChecks() = supervisorScope {
    val address = RoomAddress("https://service.example.invalid/")
    val owner = UUID.randomUUID()
    val profile = AccountProfile("A".repeat(43), "Owner", "owner@example.invalid")
    fun device(value: String, origin: URI = address.origin, source: UUID = owner) =
        AccountDeviceSession(profile, value.repeat(43), "R".repeat(43), origin, source)
    val authority = RoomAccountAuthority(address, device("B"), owner)
    try {
        val first = authority.credential(); check(!first.toString().contains(first.access))
        authority.pause()
        val cancelled = async(start = CoroutineStart.UNDISPATCHED) { authority.credential() }; val survivor = async(start = CoroutineStart.UNDISPATCHED) { authority.credential() }
        cancelled.cancel(); authority.commit(device("C"))
        try { cancelled.await(); error("Cancelled request acquired credentials") } catch (_: CancellationException) { }
        val rotated = survivor.await(); check(rotated.access == "C".repeat(43))
        fun reject(code: String, action: () -> Unit) {
            try { action(); error("Room account authority accepted $code") } catch (error: RoomProtocolException) { check(error.code == code) }
        }
        reject("malformed") { authority.commit(device("D", URI("https://other.example.invalid/"))) }
        reject("malformed") { authority.commit(device("D", source = UUID.randomUUID())) }
        reject("malformed") { authority.commit(AccountDeviceSession(AccountProfile("Z".repeat(43), "Other", "other@example.invalid"), "D".repeat(43), "R".repeat(43), address.origin, owner)) }
        check(authority.credential().access == rotated.access)
        authority.pause(); val failed = async(start = CoroutineStart.UNDISPATCHED) { authority.credential() }; authority.unavailable()
        try { failed.await(); error("Unavailable account authorized a room") } catch (error: RoomProtocolException) { check(error.code == "transport") }
        authority.pause(); val recovering = async(start = CoroutineStart.UNDISPATCHED) { authority.credential() }; authority.commit(device("E"))
        check(recovering.await().access == "E".repeat(43))
        authority.pause(); val closing = async(start = CoroutineStart.UNDISPATCHED) { authority.credential() }; authority.close()
        try { closing.await(); error("Closed authority authorized a room") } catch (error: RoomProtocolException) { check(error.code == "closed") }
        reject("closed") { authority.commit(device("F")) }
        authority.close()
        println("SYNCWATCH_ROOM_ACCOUNT_AUTHORITY_OK")
    } finally { authority.close() }
}
