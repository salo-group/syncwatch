package syncwatch

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import syncwatch.design.LibraryEntry
import syncwatch.design.LibraryPresentation
import syncwatch.design.LibraryVisualState

internal suspend fun roomPlaybackGateChecks(scope: CoroutineScope) {
    val controller = RoomController(scope, legacyValidation = true)
    controller.editor = controller.editor.selectMode("joinRoom").edit("serviceAddress", "https://rooms.example.com").edit("username", "Peer")
    val credential = "A".repeat(64)
    controller.pasteInvitation("room-paste\r\n" + credential)
    check(controller.editor.values["roomID"] == "room-paste" && controller.editor.values["invitation"] == credential && controller.editor.isValid)
    check(controller.editor.values["serviceAddress"] == "https://rooms.example.com")
    controller.editor = controller.editor.copy(state = "connecting"); controller.pasteInvitation("invalid")
    check(controller.editor.values["invitation"] == credential)
    controller.editor = controller.editor.copy(state = "disconnected"); controller.pasteInvitation("invalid")
    check(controller.editor.values["roomID"] == "" && controller.editor.values["invitation"] == "" && !controller.editor.isValid)
    check(controller.editor.status == "roomInvitationInvalid" && controller.editor.values["username"] == "Peer")
    val source = LibraryPresentation(state = LibraryVisualState.selected,
        entries = listOf(LibraryEntry("movie", "Movie", "Movies/Movie")), selectedID = "movie", enabledActions = setOf("startWatching"))
    check(source.allows("startWatching"))
    for (state in listOf("connecting", "connected", "failed")) {
        controller.editor = controller.editor.copy(state = state)
        check(!controller.libraryPresentation(source).allows("startWatching"))
        if (state == "failed") check(controller.editor.context.values["can.leaveRoom"] == "true")
        if (state == "failed") check(controller.mediaNotice == "roomConnectionFailed")
        controller.close()
        check(controller.libraryPresentation(source).allows("startWatching"))
        check(controller.mediaNotice.isEmpty())
    }
    println("SYNCWATCH_ROOM_PLAYBACK_GATE_OK explicit exit after failure")
    println("SYNCWATCH_ROOM_INVITATION_OK atomic paste with an explicit service destination")
    completeCatalog(scope)
}

private suspend fun completeCatalog(scope: CoroutineScope) {
    val address = System.getenv("SYNCWATCH_ROOM_SERVICE") ?: return
    val room = System.getenv("SYNCWATCH_CATALOG_ROOM") ?: return
    val invitation = System.getenv("SYNCWATCH_CATALOG_INVITATION") ?: return
    val controller = RoomController(scope, legacyValidation = true)
    controller.editor = controller.editor.selectMode("joinRoom").edit("serviceAddress", address)
        .edit("username", "Linux workspace catalog").edit("roomID", room).edit("invitation", invitation)
    val peer = syncwatch.room.RoomClient(syncwatch.room.RoomAddress(address))
    try {
        controller.connect()
        withTimeout(20000) { while (controller.editor.busy) { check(controller.catalogView == null); delay(10) } }
        val view = checkNotNull(controller.catalogView)
        check(controller.connected && view.bootstrap == controller.bootstrap && view.items.size == 250)
        check(view.items.map { it.media.mediaId }.toSet() == (0 until 250).map { "movie-$it" }.toSet())
        val joined = peer.join(room, syncwatch.room.RoomSecret.fromInput(invitation), syncwatch.room.RoomIdentity("run-" + java.util.UUID.randomUUID(), "Linux catalog refresh"))
        val events = peer.openEvents(joined)
        syncwatch.room.RoomBootstrap.initialEvent(events.receive(), joined)
        withTimeout(10000) {
            while (controller.bootstrap?.snapshot?.members?.any { it.memberId == joined.memberId } != true) {
                check(controller.catalogView?.items?.size == 250); delay(10)
            }
        }
        check(controller.catalogView?.items?.size == 250 && checkNotNull(controller.bootstrap).snapshot.sequence > view.bootstrap.snapshot.sequence)
        peer.leave(joined); peer.close()
        withTimeout(10000) {
            while (controller.bootstrap?.snapshot?.members?.any { it.memberId == joined.memberId } == true) {
                check(controller.catalogView?.items?.size == 250); delay(10)
            }
        }
        controller.close()
        check(controller.catalogView == null && controller.bootstrap == null && view.items.size == 250)
        controller.close()
        println("SYNCWATCH_ROOM_CONTROLLER_CATALOG_OK native 250-item view/join refresh/leave refresh/close/repeated close")
    } finally { peer.close(); controller.close() }
}
