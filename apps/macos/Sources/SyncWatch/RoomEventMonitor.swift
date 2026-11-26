import Foundation
import SyncWatchRoom

/// Rebinds one event transport while the membership keeps its HTTP owner and command counter.
@MainActor enum RoomEventMonitor {
    private struct Connection: Sendable {
        let stream: RoomEventStream
        let view: RoomCatalogView
    }
    static func run(
        address: RoomAddress, membership: RoomMembershipSession, authority: RoomAccountAuthority? = nil,
        reconnecting: () -> Void, accepted: (RoomCatalogView) throws -> Void
    ) async throws {
        let initialDelay: Duration = .milliseconds(500)
        let maximumDelay: Duration = .seconds(4)
        var retryDelay = initialDelay
        var deadline: ContinuousClock.Instant?
        while true {
            try Task.checkCancellation()
            let client = authority.map { RoomClient(authority: $0) } ?? RoomClient(address: address)
            do {
                let allowance = deadline.map { min(.seconds(20), ContinuousClock.now.duration(to: $0)) } ?? .seconds(20)
                guard allowance > .zero else { throw RoomProtocolFailure.transport }
                let recovering = deadline != nil
                let connection = try await withThrowingTaskGroup(of: Connection.self) { group in
                    group.addTask {
                        let (stream, view) = try await membership.connectEvents(client, recovering: recovering)
                        return Connection(stream: stream, view: view)
                    }
                    group.addTask {
                        try await Task.sleep(for: allowance)
                        throw RoomProtocolFailure.transport
                    }
                    defer { group.cancelAll() }
                    return try await group.next()!
                }
                try Task.checkCancellation()
                try accepted(connection.view)
                retryDelay = initialDelay
                deadline = nil
                var previous = connection.view.bootstrap.snapshot
                while true {
                    let event = try RoomInvalidation.decode(await connection.stream.receive())
                    guard event.epoch == previous.epoch else { throw RoomProtocolFailure.malformed }
                    if event.sequence <= previous.sequence { continue }
                    let view = try await membership.refresh(sequence: event.sequence)
                    try Task.checkCancellation()
                    try accepted(view)
                    previous = view.bootstrap.snapshot
                }
            } catch {
                await client.close()
                try Task.checkCancellation()
                guard RoomController.isMetadataInterruption(error) else { throw error }
                if deadline == nil { deadline = ContinuousClock.now + .seconds(25) }
                guard let deadline, ContinuousClock.now + retryDelay < deadline else { throw error }
                reconnecting()
                try await Task.sleep(for: retryDelay)
                retryDelay = min(retryDelay * 2, maximumDelay)
            }
        }
    }
}
