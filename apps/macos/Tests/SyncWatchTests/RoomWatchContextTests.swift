import Foundation
import SyncWatchDomain
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_SERVICE"] != nil))
func preparedPublicationCannotEscapeItsMembership() async throws {
    let environment = ProcessInfo.processInfo.environment
    let controller = RoomController(legacyValidation: true)
    for (field, value) in [
        "serviceAddress": try #require(environment["SYNCWATCH_ROOM_SERVICE"]), "username": "macOS prepared publisher",
        "creationKey": try #require(environment["SYNCWATCH_ROOM_CREATION_KEY"]),
        "syncplayHost": "syncplay.example.com", "roomName": "Prepared publication lifecycle",
    ] { controller.editor.edit(field, value: value) }
    func wait(_ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(20)
        while !condition() {
            guard controller.editor.state != "failed", ContinuousClock.now < deadline else {
                throw RoomProtocolFailure.transport
            }
            try await Task.sleep(for: .milliseconds(20))
        }
    }
    do {
        controller.connect()
        try await wait { controller.connected }
        let intent = try await controller.beginWatch()
        let media = try MediaDescriptor(
            mediaID: "private-loaded-file", title: "Loaded", titleKey: "loaded",
            fileSizeBytes: 1000, durationMs: 60000, fullHashSha256: String(repeating: "a", count: 64))
        let first = try await controller.publishPrepared(media, membership: intent.membership)
        #expect(first.media.mediaID != media.mediaID && first.media.fullHashSha256 == media.fullHashSha256)
        #expect(controller.canContinuePlayback(intent.membership))
        var context = try await controller.performWatch(
            .select(mediaID: first.media.mediaID), membership: intent.membership, expected: intent.context)
        context = try await controller.performWatch(
            .start(mediaID: first.media.mediaID), membership: intent.membership, expected: context)
        let empty = RoomController(legacyValidation: true)
        do {
            empty.editor.selectMode("joinRoom")
            empty.editor.edit("serviceAddress", value: try #require(environment["SYNCWATCH_ROOM_SERVICE"]))
            empty.editor.edit("username", value: "macOS empty library")
            empty.pasteInvitation(try #require(controller.invitationToCopy()))
            empty.connect()
            try await wait {
                empty.mediaNotice == "sharedWatchMediaMissing" && controller.mediaNotice == "roomPeersMissingMedia"
            }
            await empty.close()
            try await wait { controller.mediaNotice.isEmpty }
        } catch {
            await empty.close()
            throw error
        }
        _ = try await controller.performWatch(.end(reason: .user), membership: intent.membership, expected: context)
        #expect(controller.canContinuePlayback(intent.membership))
        let second = try await controller.publishPrepared(media, membership: intent.membership)
        #expect(second.media == first.media && second.lease != first.lease)
        controller.clearPrepared(first.lease, membership: intent.membership)
        #expect(controller.catalogView?.items.contains { $0.media == second.media } == true)
        controller.clearPrepared(second.lease, membership: intent.membership)
        try await wait { controller.catalogView?.items.isEmpty == true }
        await controller.close()
        #expect(
            !controller.isCurrent(intent.membership) && !controller.canContinuePlayback(intent.membership)
                && controller.watchBootstrap == nil)
        do {
            _ = try await controller.publishPrepared(media, membership: intent.membership)
            Issue.record("Stale membership published")
        } catch RoomProtocolFailure.malformed {}
        await controller.close()
    } catch {
        await controller.close()
        throw error
    }
}

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_SERVICE"] != nil))
func watchIntentUsesTheMembershipCounterAndFrozenEdition() async throws {
    let environment = ProcessInfo.processInfo.environment
    let address = try RoomAddress(#require(environment["SYNCWATCH_ROOM_SERVICE"]))
    let client = RoomClient(address: address)
    let peer = RoomClient(address: address)
    do {
        let identity = try RoomIdentity(clientRunID: "run-" + UUID().uuidString, syncplayUsername: "macOS watch")
        let admission = try await client.create(
            identity: identity,
            connection: RoomConnection(host: "syncplay.example.com", port: 8999, room: "Watch context"),
            creationKey: RoomSecret(#require(environment["SYNCWATCH_ROOM_CREATION_KEY"])))
        let stream = try await client.openEvents(admission)
        _ = try RoomBootstrap.initialEvent(await stream.receive(), admission: admission)
        let owner = RoomMembershipSession(client: client, admission: admission, identity: identity)
        let descriptor = try MediaDescriptor(
            mediaID: "movie-a", title: "Movie", titleKey: "movie", fileSizeBytes: 1000,
            durationMs: 60000, fullHashSha256: String(repeating: "a", count: 64))
        _ = try await owner.publish([descriptor])
        let idle = try await owner.watchContext()
        #expect(idle.definition.selection == nil)
        let selected = try await owner.perform(.select(mediaID: "movie-a"), expected: idle)
        #expect(selected.definition.selection?.media == descriptor)
        _ = try await owner.publish([])
        #expect(try await owner.watchContext().definition.selection?.media == descriptor)
        let peerIdentity = try RoomIdentity(clientRunID: "run-" + UUID().uuidString, syncplayUsername: "macOS peer")
        let joined = try await peer.join(
            roomID: admission.roomID, invitation: #require(admission.invitation), identity: peerIdentity)
        let peerStream = try await peer.openEvents(joined)
        _ = try RoomBootstrap.initialEvent(await peerStream.receive(), admission: joined)
        let peerOwner = RoomMembershipSession(client: peer, admission: joined, identity: peerIdentity)
        _ = try await peerOwner.publish([descriptor])
        _ = try await peerOwner.perform(.select(mediaID: "movie-a"), expected: peerOwner.watchContext())
        do {
            _ = try await owner.perform(.start(mediaID: "movie-a"), expected: selected)
            Issue.record("A stale user intent overwrote the peer's generation")
        } catch RoomProtocolFailure.rejected("staleGeneration") {}
        let current = try await owner.watchContext()
        let active = try await owner.perform(.start(mediaID: "movie-a"), expected: current)
        #expect(active.bootstrap.snapshot.watchStatus == "active")
        #expect(active.definition.selection?.memberID == joined.memberID)
        let renamed = try MediaDescriptor(
            mediaID: "private-local-id", title: "Renamed", titleKey: "renamed",
            fileSizeBytes: 1000, durationMs: 60000, fullHashSha256: String(repeating: "a", count: 64))
        try RoomWatchCoordinator.requireMatch(renamed, active)
        #expect(throws: (any Error).self) { try RoomWatchCoordinator.requireMatch(renamed, selected) }
        #expect(throws: (any Error).self) { try RoomWatchCoordinator.requireSameGeneration(selected, active) }
        for (duration, hash) in [
            (Int64(60000), String(repeating: "b", count: 64)), (Int64(60001), String(repeating: "a", count: 64)),
        ] {
            let changed = try MediaDescriptor(
                mediaID: "private-local-id", title: "Movie", titleKey: "movie",
                fileSizeBytes: 1000, durationMs: duration, fullHashSha256: hash)
            #expect(throws: (any Error).self) { try RoomWatchCoordinator.requireMatch(changed, active) }
        }
        let weak = try MediaDescriptor(
            mediaID: "private-local-id", title: "Movie", titleKey: "movie",
            fileSizeBytes: 1000, durationMs: 60000, fullHashSha256: nil)
        #expect(throws: (any Error).self) { try RoomWatchCoordinator.requireMatch(weak, active) }
        let finished = try await owner.perform(.end(reason: .finished), expected: active)
        #expect(RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, active.bootstrap.snapshot))
        #expect(RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, finished.bootstrap.snapshot))
        let next = try await owner.perform(.select(mediaID: "movie-a"), expected: finished)
        #expect(!RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, next.bootstrap.snapshot))
        let restarted = try await owner.perform(.start(mediaID: "movie-a"), expected: next)
        #expect(
            !RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, restarted.bootstrap.snapshot))
        let laterEnd = try await owner.perform(.end(reason: .finished), expected: restarted)
        #expect(
            !RoomWatchCoordinator.completedPlaylistAvailable(active.bootstrap.snapshot, laterEnd.bootstrap.snapshot))
        #expect(
            RoomWatchCoordinator.completedPlaylistAvailable(restarted.bootstrap.snapshot, laterEnd.bootstrap.snapshot))
        try await peer.leave(joined)
        try await client.leave(admission)
        await peer.close()
        await client.close()
    } catch {
        await peer.close()
        await client.close()
        throw error
    }
}

@Test @MainActor func metadataFailureClassificationDoesNotPreserveInvalidMemberships() {
    #expect(RoomController.isMetadataInterruption(URLError(.networkConnectionLost)))
    #expect(RoomController.isMetadataInterruption(RoomProtocolFailure.transport))
    for error in [RoomProtocolFailure.malformed, .rejected("unauthorized"), .rejected("forbidden"), .frameTooLarge] {
        #expect(!RoomController.isMetadataInterruption(error))
    }
}

@Test @MainActor func sharedWatchFailurePreservesExplicitIntentAndPrivateDetails() {
    #expect(
        RoomWatchCoordinator.failureKey(SharedMediaAvailabilityFailure.unavailable, identityRejected: false)
            == "sharedWatchMediaMissing")
    #expect(
        RoomWatchCoordinator.failureKey(SharedMediaAvailabilityFailure.unavailable, identityRejected: true)
            == "sharedWatchIdentityRejected")
    #expect(
        RoomWatchCoordinator.failureKey(RoomProtocolFailure.rejected("staleGeneration"), identityRejected: false)
            == "sharedWatchRoomChanged")
    #expect(
        RoomWatchCoordinator.failureKey(RoomProtocolFailure.rejected("staleGeneration"), identityRejected: true)
            == "sharedWatchIdentityRejected")
    for error: Error in [
        RoomProtocolFailure.rejected("unauthorized"), URLError(.networkConnectionLost),
        NSError(domain: "/private/movie/token", code: 1),
    ] {
        #expect(RoomWatchCoordinator.failureKey(error, identityRejected: false) == "sharedWatchFailed")
    }
}
