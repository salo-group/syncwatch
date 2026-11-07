package syncwatch

import java.util.UUID
import kotlinx.coroutines.*
import syncwatch.domain.MediaDescriptor
import syncwatch.room.*

internal suspend fun roomWatchContextChecks() {
    admissionIsolationChecks()
    check(RoomWatchCoordinator.failureKey(SharedMediaUnavailable(), false) == "sharedWatchMediaMissing")
    check(RoomWatchCoordinator.failureKey(SharedMediaUnavailable(), true) == "sharedWatchIdentityRejected")
    check(RoomController.isMetadataInterruption(java.io.IOException()))
    check(RoomController.isMetadataInterruption(java.util.concurrent.CompletionException(java.io.IOException())))
    check(!RoomController.isMetadataInterruption(java.util.concurrent.CompletionException(RoomProtocolException("unauthorized"))))
    check(RoomController.isMetadataInterruption(RoomProtocolException("transport")))
    for (code in listOf("malformed", "unauthorized", "forbidden", "frameTooLarge")) check(!RoomController.isMetadataInterruption(RoomProtocolException(code)))
    check(RoomWatchCoordinator.failureKey(RoomProtocolException("staleGeneration"), false) == "sharedWatchRoomChanged")
    check(RoomWatchCoordinator.failureKey(RoomProtocolException("staleGeneration"), true) == "sharedWatchIdentityRejected")
    for (error in listOf(RoomProtocolException("unauthorized"), java.io.IOException(), Exception("/private/movie/token")))
        check(RoomWatchCoordinator.failureKey(error, false) == "sharedWatchFailed")
    val address = System.getenv("SYNCWATCH_ROOM_SERVICE") ?: return
    val key = System.getenv("SYNCWATCH_ROOM_CREATION_KEY") ?: return
    roomEventMonitorChecks(address, key)
    preparedPublication(address, key)
    val client = RoomClient(RoomAddress(address)); val peer = RoomClient(RoomAddress(address))
    try {
        val identity = RoomIdentity("run-" + UUID.randomUUID(), "Linux watch")
        val admission = client.create(identity, RoomConnection("syncplay.example.com", 8999, "Watch context"), RoomSecret(key))
        RoomBootstrap.initialEvent(client.openEvents(admission).receive(), admission)
        val owner = RoomMembershipSession(client, admission, identity)
        val descriptor = MediaDescriptor("movie-a", "Movie", "movie", 1000, 60000, "a".repeat(64))
        owner.publish(listOf(descriptor))
        val idle = owner.watchContext(); check(idle.definition.selection == null)
        val selected = owner.perform(RoomCommandAction.Select("movie-a"), idle)
        check(selected.definition.selection?.media == descriptor)
        owner.publish(emptyList())
        check(owner.watchContext().definition.selection?.media == descriptor)
        val peerIdentity = RoomIdentity("run-" + UUID.randomUUID(), "Linux peer")
        val joined = peer.join(admission.roomId, checkNotNull(admission.invitation), peerIdentity)
        RoomBootstrap.initialEvent(peer.openEvents(joined).receive(), joined)
        val peerOwner = RoomMembershipSession(peer, joined, peerIdentity)
        peerOwner.publish(listOf(descriptor))
        peerOwner.perform(RoomCommandAction.Select("movie-a"), peerOwner.watchContext())
        check((runCatching { owner.perform(RoomCommandAction.Start("movie-a"), selected) }.exceptionOrNull() as? RoomProtocolException)?.code == "staleGeneration")
        val active = owner.perform(RoomCommandAction.Start("movie-a"), owner.watchContext())
        check(active.bootstrap.snapshot.watchStatus == "active" && active.definition.selection?.memberId == joined.memberId)
        val renamed = MediaDescriptor("private-local-id", "Renamed", "renamed", 1000, 60000, "a".repeat(64))
        RoomWatchCoordinator.requireMatch(renamed, active)
        check(runCatching { RoomWatchCoordinator.requireMatch(renamed, selected) }.isFailure)
        check(runCatching { RoomWatchCoordinator.requireSameGeneration(selected, active) }.isFailure)
        for (changed in listOf(
            MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60000, "b".repeat(64)),
            MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60001, "a".repeat(64)),
            MediaDescriptor("private-local-id", "Movie", "movie", 1000, 60000, null))) {
            check(runCatching { RoomWatchCoordinator.requireMatch(changed, active) }.isFailure)
        }
        val finished = owner.perform(RoomCommandAction.End(RoomWatchEndReason.finished), active)
        check(RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, active.bootstrap.snapshot))
        check(RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, finished.bootstrap.snapshot))
        val next = owner.perform(RoomCommandAction.Select("movie-a"), finished)
        check(!RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, next.bootstrap.snapshot))
        val restarted = owner.perform(RoomCommandAction.Start("movie-a"), next)
        check(!RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, restarted.bootstrap.snapshot))
        val laterEnd = owner.perform(RoomCommandAction.End(RoomWatchEndReason.finished), restarted)
        check(!RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, laterEnd.bootstrap.snapshot))
        check(RoomWatchCoordinator.completedPlaylistAvailable(restarted.bootstrap.snapshot, laterEnd.bootstrap.snapshot))
        peer.leave(joined); client.leave(admission)
        println("SYNCWATCH_ROOM_WATCH_CONTEXT_OK")
    } finally { peer.close(); client.close() }
}

private suspend fun preparedPublication(address: String, key: String) = coroutineScope {
    val controller = RoomController(this, legacyValidation = true)
    for ((field, value) in mapOf("serviceAddress" to address, "username" to "Linux prepared publisher", "creationKey" to key,
        "syncplayHost" to "syncplay.example.com", "roomName" to "Prepared publication lifecycle")) controller.editor = controller.editor.edit(field, value)
    try {
        controller.connect()
        withTimeout(20000) { while (!controller.connected) { check(controller.editor.state != "failed"); delay(20) } }
        val (ticket, intent) = controller.beginWatch()
        val media = MediaDescriptor("private-loaded-file", "Loaded", "loaded", 1000, 60000, "a".repeat(64))
        val first = controller.publishPrepared(media, ticket)
        check(first.media.mediaId != media.mediaId && first.media.fullHashSha256 == media.fullHashSha256)
        check(controller.canContinuePlayback(ticket))
        var context = controller.performWatch(RoomCommandAction.Select(first.media.mediaId), ticket, intent)
        context = controller.performWatch(RoomCommandAction.Start(first.media.mediaId), ticket, context)
        val empty = RoomController(this, legacyValidation = true)
        try {
            empty.editor = empty.editor.selectMode("joinRoom").edit("serviceAddress", address).edit("username", "Linux empty library")
            empty.pasteInvitation(checkNotNull(controller.invitationToCopy())); empty.connect()
            withTimeout(10000) { while (empty.mediaNotice != "sharedWatchMediaMissing" || controller.mediaNotice != "roomPeersMissingMedia") delay(20) }
            empty.close()
            withTimeout(10000) { while (controller.mediaNotice.isNotEmpty()) delay(20) }
        } finally { empty.close() }
        controller.performWatch(RoomCommandAction.End(RoomWatchEndReason.user), ticket, context)
        check(controller.canContinuePlayback(ticket))
        val second = controller.publishPrepared(media, ticket)
        check(second.media == first.media && second.lease != first.lease)
        controller.clearPrepared(first.lease, ticket)
        check(controller.catalogView?.items?.any { it.media == second.media } == true)
        controller.clearPrepared(second.lease, ticket)
        withTimeout(20000) { while (controller.catalogView?.items?.isEmpty() != true) delay(20) }
        controller.close()
        check(!controller.isCurrent(ticket) && !controller.canContinuePlayback(ticket) && controller.watchBootstrap == null)
        check(runCatching { controller.publishPrepared(media, ticket) }.isFailure)
        controller.close()
        println("SYNCWATCH_PREPARED_PUBLICATION_OK opaque strong ACK/new lease/stale cleanup/leave/repeated close")
    } finally { controller.close() }
}
