package syncwatch.room

import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID

suspend fun roomAccountTransportChecks() {
    val path = System.getenv("SYNCWATCH_ROOM_ACCOUNT_FIXTURE") ?: run {
        println("SKIP owned authenticated room service: SYNCWATCH_ROOM_ACCOUNT_FIXTURE is not set"); return
    }
    val fixture = RoomWire.parse(Files.readAllBytes(Path.of(path)))
    val ownerRecord = AccountSavedSession.decode(RoomWire.encode(fixture["owner"]))
    val guestRecord = AccountSavedSession.decode(RoomWire.encode(fixture["guest"]))
    val ownerAccount = AccountClient(RoomAddress(ownerRecord.service)); val guestAccount = AccountClient(RoomAddress(guestRecord.service))
    var owner: RoomAccountAuthority? = null; var guest: RoomAccountAuthority? = null
    var host: RoomClient? = null; var peer: RoomClient? = null
    try {
        val ownerDevice = ownerAccount.resume(ownerRecord); val guestDevice = guestAccount.resume(guestRecord)
        val hostAuthority = ownerAccount.roomAuthority(ownerDevice); val guestAuthority = guestAccount.roomAuthority(guestDevice)
        owner = hostAuthority; guest = guestAuthority
        val ownerRoom = RoomClient(hostAuthority); val guestRoom = RoomClient(guestAuthority)
        host = ownerRoom; peer = guestRoom
        val admission = ownerRoom.create("linux-${UUID.randomUUID()}", "create-${UUID.randomUUID()}")
        check(admission.isAccountBacked)
        val hostEvents = ownerRoom.openEvents(admission)
        RoomBootstrap.initialEvent(hostEvents.receive(), admission)
        val invitation = ownerRoom.invite(admission)
        val attempt = guestRoom.requestAdmission(invitation, "guest-${UUID.randomUUID()}", "join-${UUID.randomUUID()}")
        check(attempt.request.account == guestDevice.account)
        check(ownerRoom.pending(admission).single().id == attempt.request.id)
        try { guestRoom.accept(attempt); error("Unapproved device admitted") } catch (error: RoomProtocolException) { check(error.code == "admissionRequired") }
        ownerRoom.decide(admission, attempt.request.id, true)
        check(guestRoom.admissionStatus(attempt).status == "approved")
        val joined = guestRoom.accept(attempt); val events = guestRoom.openEvents(joined)
        check(RoomBootstrap.initialEvent(events.receive(), joined).snapshot.members.size == 2)
        check(RoomWire.parse(hostEvents.receive())["type"] == "member.bound")
        val current = ownerRoom.snapshot(admission); check(current.snapshot.members.size == 2)
        check(ownerRoom.catalog(admission, current, current.catalogCursor).items.isEmpty())
        val first = guestRoom.syncTicket(joined); val second = guestRoom.syncTicket(joined)
        check(first.credential.value != second.credential.value)
        val withdrawal = guestRoom.requestAdmission(ownerRoom.invite(admission), "withdraw-${UUID.randomUUID()}", "withdraw-${UUID.randomUUID()}")
        guestRoom.cancelAdmission(withdrawal); guestRoom.cancelAdmission(withdrawal)
        check(guestRoom.admissionStatus(withdrawal).status == "revoked")
        val race = guestRoom.requestAdmission(ownerRoom.invite(admission), "race-${UUID.randomUUID()}", "race-${UUID.randomUUID()}")
        ownerRoom.decide(admission, race.request.id, true)
        guestRoom.accept(race)
        guestRoom.cancelAdmission(race)
        check(ownerRoom.snapshot(admission).snapshot.members.size == 2)
        check(guestRoom.admissionStatus(race).status == "revoked")
        hostAuthority.pause()
        val rotated = ownerAccount.resume(ownerAccount.checkpoint(ownerDevice))
        ownerAccount.checkpoint(rotated); hostAuthority.commit(rotated)
        ownerRoom.snapshot(admission)
        guestAccount.logout(guestDevice)
        try { guestRoom.snapshot(joined); error("Logged-out device retained access") } catch (error: RoomProtocolException) { check(error.code == "unauthorized") }
        ownerRoom.leave(admission); ownerAccount.logout(rotated)
        println("SYNCWATCH_AUTHENTICATED_ROOM_TRANSPORT_OK")
    } finally { host?.close(); peer?.close(); owner?.close(); guest?.close(); ownerAccount.close(); guestAccount.close() }
}
