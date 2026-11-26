import Foundation
import SyncWatchDomain
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_SERVICE"] != nil))
func eventRecoveryRetainsMembershipAndCommandNumbering() async throws {
    let environment = ProcessInfo.processInfo.environment
    let address = try RoomAddress(#require(environment["SYNCWATCH_ROOM_SERVICE"]))
    let client = RoomClient(address: address)
    var monitoring: Task<Void, Error>?
    var publishing: Task<RoomCatalogView, Error>?
    do {
        let identity = try RoomIdentity(clientRunID: "run-" + UUID().uuidString, syncplayUsername: "macOS recovery")
        let admission = try await client.create(
            identity: identity, connection: RoomConnection(host: "syncplay.example.com", port: 8999, room: "Recovery"),
            creationKey: RoomSecret(#require(environment["SYNCWATCH_ROOM_CREATION_KEY"])))
        let owner = RoomMembershipSession(client: client, admission: admission, identity: identity)
        let media = try MediaDescriptor(
            mediaID: "movie", title: "Movie", titleKey: "movie", fileSizeBytes: 10, durationMs: 60000,
            fullHashSha256: nil)
        _ = try await owner.publish([media])
        let corpus = try (0..<120).map { index in
            try MediaDescriptor(
                mediaID: "extra-\(index)", title: "Extra", titleKey: "extra", fileSizeBytes: 10,
                durationMs: 60000, fullHashSha256: nil)
        }
        publishing = Task { try await owner.publish([media] + corpus) }
        var outageStarted: ContinuousClock.Instant?
        var deliveries = 0
        var recovered = false
        var recoveries = 0
        monitoring = Task {
            try await RoomEventMonitor.run(
                address: address, membership: owner, reconnecting: { recoveries += 1 },
                accepted: { view in
                    #expect(
                        view.bootstrap.snapshot.members.contains {
                            $0.memberID == admission.memberID && $0.identity == identity
                        })
                    let now = ContinuousClock.now
                    let start = outageStarted ?? now
                    outageStarted = start
                    deliveries += 1
                    guard deliveries > 6, start.duration(to: now) >= .seconds(20) else {
                        throw RoomProtocolFailure.transport
                    }
                    recovered = true
                })
        }
        _ = try await publishing?.value
        publishing = nil
        let until = ContinuousClock.now + .seconds(28)
        while !recovered {
            guard ContinuousClock.now < until else { throw RoomProtocolFailure.transport }
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(recoveries > 0)
        _ = try await owner.publish([media])
        let context = try await owner.watchContext()
        let selected = try await owner.perform(.select(mediaID: media.mediaID), expected: context)
        #expect(selected.definition.selection?.media == media)
        monitoring?.cancel()
        _ = await monitoring?.result
        monitoring = nil
        var exhaustedAttempts = 0
        var exhaustedRetries = 0
        let exhaustionStarted = ContinuousClock.now
        do {
            try await RoomEventMonitor.run(
                address: address, membership: owner,
                reconnecting: { exhaustedRetries += 1 },
                accepted: { _ in
                    exhaustedAttempts += 1
                    throw RoomProtocolFailure.transport
                })
            Issue.record("Transport retries were not bounded")
        } catch RoomProtocolFailure.transport {}
        #expect((1...8).contains(exhaustedRetries) && exhaustedAttempts <= exhaustedRetries + 1)
        #expect(exhaustionStarted.duration(to: .now) < .seconds(28))
        var retries = 0
        do {
            try await RoomEventMonitor.run(
                address: address, membership: owner, reconnecting: { retries += 1 },
                accepted: { _ in throw RoomProtocolFailure.malformed })
            Issue.record("Malformed metadata was retried")
        } catch RoomProtocolFailure.malformed {}
        #expect(retries == 0)
        try await client.leave(admission)
        await client.close()
    } catch {
        publishing?.cancel()
        _ = await publishing?.result
        monitoring?.cancel()
        _ = await monitoring?.result
        await client.close()
        throw error
    }
}
