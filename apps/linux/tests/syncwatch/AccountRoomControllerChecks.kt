package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.*
import syncwatch.room.*

internal suspend fun accountRoomControllerChecks(scope: CoroutineScope) {
    val path = System.getenv("SYNCWATCH_ROOM_ACCOUNT_APP_FIXTURE") ?: run {
        println("SKIP account room application: no owned service fixture"); return
    }
    val fixture = RoomWire.parse(Files.readAllBytes(Path.of(path)))
    val ownerRecord = AccountSavedSession.decode(RoomWire.encode(fixture["owner"]))
    val guestRecord = AccountSavedSession.decode(RoomWire.encode(fixture["guest"]))
    val ownerClient = AccountClient(RoomAddress(ownerRecord.service)); val guestClient = AccountClient(RoomAddress(guestRecord.service))
    val ownerDevice = ownerClient.resume(ownerRecord); val guestDevice = guestClient.resume(guestRecord)
    val ownerAuthority = ownerClient.roomAuthority(ownerDevice); val guestAuthority = guestClient.roomAuthority(guestDevice)
    var ownerSource: RoomAccountAuthority? = ownerAuthority; var guestSource: RoomAccountAuthority? = guestAuthority
    val host = RoomController(scope, accountSource = { ownerSource }); val peer = RoomController(scope, accountSource = { guestSource })
    try {
        check(!host.editor.isValid && host.editor.requiresAccount)
        host.connect(); check(host.editor.state == "disconnected")
        host.bindAccount(ownerAuthority, ownerDevice.account.name); peer.bindAccount(guestAuthority, guestDevice.account.name)
        host.connect(); roomSettles { host.connected }
        check(host.editor.owner && host.editor.accountName == ownerDevice.account.name)
        val superseded = checkNotNull(host.invitationForCopy()); checkNotNull(host.invitationForCopy()); check(!host.canCopy(superseded))
        for (outcome in listOf("cancel", "deny", "approve")) {
            val transfer = checkNotNull(host.invitationForCopy()); check(host.canCopy(transfer))
            peer.editor = peer.editor.selectMode("joinRoom"); peer.pasteInvitation(transfer.invitation.copyText()); peer.connect()
            roomSettles { peer.editor.state == "awaitingApproval" && host.editor.pendingRequests.size == 1 && host.editor.pendingAvailable }
            check(host.editor.pendingRequests[0].name == guestDevice.account.name && host.editor.pendingRequests[0].email == guestDevice.account.email)
            if (outcome == "cancel") {
                peer.close(); peer.close(); roomSettles { host.editor.pendingRequests.isEmpty() }; check(host.connected)
            } else {
                host.decideAdmission(host.editor.pendingRequests[0].id, outcome == "approve")
                if (outcome == "deny") {
                    roomSettles { peer.editor.state == "failed" }; check(peer.editor.status == "roomAdmissionDenied"); peer.close()
                } else {
                    roomSettles { peer.connected && host.editor.memberCount == 2 }; check(!peer.editor.owner)
                    val membership = peer.beginWatch().first
                    check(peer.canContinuePlayback(membership))
                    (fixture["browserFixtureToken"] as? String)?.let { token ->
                        meetApplicationChecks(scope, host, peer, java.net.URI(ownerRecord.service), token, ownerDevice.account) { available ->
                            guestSource = if (available) guestAuthority else null; ownerSource = if (available) ownerAuthority else null
                        }
                    }
                    guestSource = null
                    check(!peer.connected && !peer.canContinuePlayback(membership)); ownerSource = null
                    check(!host.connected && !host.canCopy(transfer))
                    peer.close(); host.close(); host.invitationCopyCompleted(true, transfer)
                    check(host.editor.state == "disconnected" && host.editor.status.isEmpty())
                }
            }
        }
        guestClient.logout(guestDevice); ownerClient.logout(ownerDevice)
        println("SYNCWATCH_ACCOUNT_ROOM_APPLICATION_OK")
    } finally {
        withContext(NonCancellable) {
            peer.close(); host.close(); guestAuthority.close(); ownerAuthority.close(); guestClient.close(); ownerClient.close()
        }
    }
}
private suspend fun roomSettles(condition: () -> Boolean) = withTimeout(20_000) { while (!condition()) delay(20) }
