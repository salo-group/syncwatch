import Foundation
import SyncWatchDomain
import SyncWatchRoom

struct RoomWatchContext: Sendable {
    let bootstrap: RoomBootstrap
    let definition: RoomSelectionDefinition
}

/// One application-owned membership serializes bootstrap walks and command numbering.
@MainActor final class RoomMembershipSession {
    private let client: RoomClient
    private let admission: RoomAdmission
    private let identity: RoomIdentity
    private var minimumSequence: UInt64
    private var number: UInt64 = 0
    private var unresolved: RoomCommand?
    private var occupied = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(client: RoomClient, admission: RoomAdmission, identity: RoomIdentity) {
        self.client = client
        self.admission = admission
        self.identity = identity
        minimumSequence = admission.bootstrap.snapshot.sequence
    }

    func invitation() async throws -> RoomInvitation { try await client.invite(admission) }
    func pendingAdmissions() async throws -> [RoomAdmissionRequest] { try await client.pending(admission) }
    func decideAdmission(_ id: String, approve: Bool) async throws { try await client.decide(admission, approvalID: id, approve: approve) }
    func syncTicket() async throws -> RoomSyncTicket { try await client.syncTicket(admission) }
    // Calls have their own aggregate and do not acquire the ordered catalog/watch lock.
    func callStatus() async throws -> RoomCallStatus { try await client.callStatus(admission) }
    func callStartOperation() async throws -> RoomCallOperation { try await client.callStartOperation(admission) }
    func callEndOperation(_ status: RoomCallStatus) async throws -> RoomCallOperation { try await client.callEndOperation(admission, status: status) }
    func requestCall(_ operation: RoomCallOperation) async throws -> RoomCallStatus { try await client.requestCall(operation, admission: admission) }
    func beginMeetAuthorization() async throws -> RoomMeetAuthorization { try await client.beginMeetAuthorization(admission) }
    func meetAuthorizationStatus(_ flow: RoomMeetAuthorization) async throws -> RoomMeetAuthorizationStatus { try await client.meetAuthorizationStatus(flow, admission: admission) }
    func verifyMeetAuthorization(_ flow: RoomMeetAuthorization, code: String) async throws -> RoomMeetAuthorizationStatus { try await client.verifyMeetAuthorization(flow, admission: admission, code: code) }
    func confirmMeetAuthorization(_ flow: RoomMeetAuthorization, code: String) async throws -> RoomMeetAuthorizationStatus { try await client.confirmMeetAuthorization(flow, admission: admission, code: code) }
    func cancelMeetAuthorization(_ flow: RoomMeetAuthorization) async throws -> RoomMeetAuthorizationStatus { try await client.cancelMeetAuthorization(flow, admission: admission) }

    func refresh(initial: RoomBootstrap? = nil, sequence: UInt64 = 0) async throws -> RoomCatalogView {
        try await enter()
        defer { leave() }
        minimumSequence = max(minimumSequence, sequence)
        return try await read(initial)
    }

    /// Creating a socket bootstrap also replaces the server's catalog cursor.
    func connectEvents(_ events: RoomClient, recovering: Bool) async throws -> (RoomEventStream, RoomCatalogView) {
        try await enter()
        defer { leave() }
        if recovering { _ = try await validatedSnapshot() }
        let stream = try await events.openEvents(admission)
        _ = try RoomBootstrap.initialEvent(await stream.receive(), admission: admission)
        // Commands may have advanced the sequence floor before this lock was acquired.
        return (stream, try await read(nil))
    }

    func publish(_ items: [MediaDescriptor]) async throws -> RoomCatalogView {
        try await enter()
        defer { leave() }
        guard items.count <= 2048, Set(items.map(\.mediaID)).count == items.count else {
            throw RoomProtocolFailure.malformed
        }
        // An uncertain request keeps its original ID/body even if the local library changed.
        if unresolved != nil { try await acknowledge() }
        for attempt in 0..<2 {
            let snapshot = try await validatedSnapshot()
            let batch = "batch-" + UUID().uuidString
            var offset = 0
            do {
                repeat {
                    let command = try Self.catalogCommand(
                        admission: admission, number: number + 1, generation: snapshot.snapshot.watchGeneration,
                        batch: batch, items: items, offset: offset)
                    number += 1
                    unresolved = command.command
                    try await acknowledge()
                    offset += command.count
                } while offset < items.count
                return try await read(nil)
            } catch RoomProtocolFailure.rejected("staleGeneration") where attempt == 0 {
                // The NACK consumed its ID. Start a new atomic batch from authoritative metadata.
                continue
            }
        }
        throw RoomProtocolFailure.rejected("staleGeneration")
    }

    func watchContext() async throws -> RoomWatchContext {
        try await enter()
        defer { leave() }
        if unresolved != nil { try await acknowledge() }
        return try await readWatchContext()
    }

    /// A peer generation change requires an explicit new user decision.
    func perform(_ action: RoomCommandAction, expected: RoomWatchContext) async throws -> RoomWatchContext {
        try await enter()
        defer { leave() }
        if unresolved != nil { try await acknowledge() }
        let current = try await validatedSnapshot()
        guard current.snapshot.epoch == expected.bootstrap.snapshot.epoch,
            current.snapshot.watchGeneration == expected.bootstrap.snapshot.watchGeneration
        else { throw RoomProtocolFailure.rejected("staleGeneration") }
        let command = try RoomCommand(
            admission: admission, number: number + 1,
            watchGeneration: current.snapshot.watchGeneration, action: action)
        number += 1
        unresolved = command
        try await acknowledge()
        return try await readWatchContext()
    }

    private func readWatchContext() async throws -> RoomWatchContext {
        for attempt in 0..<2 {
            let bootstrap = try await validatedSnapshot()
            do {
                let definition = try await client.selection(admission, bootstrap: bootstrap)
                try Task.checkCancellation()
                minimumSequence = max(minimumSequence, definition.sequence)
                return RoomWatchContext(bootstrap: bootstrap, definition: definition)
            } catch RoomProtocolFailure.rejected("staleGeneration") where attempt == 0 { continue }
        }
        throw RoomProtocolFailure.rejected("staleGeneration")
    }

    static func catalogCommand(
        admission: RoomAdmission, number: UInt64, generation: UInt64, batch: String,
        items: [MediaDescriptor], offset: Int
    ) throws -> (command: RoomCommand, count: Int) {
        guard offset >= 0, offset <= items.count else { throw RoomProtocolFailure.malformed }
        var count = min(100, items.count - offset)
        while true {
            do {
                let command = try RoomCommand(
                    admission: admission, number: number, watchGeneration: generation,
                    action: .catalog(
                        batchID: batch, offset: UInt64(offset), items: Array(items[offset..<(offset + count)]),
                        final: offset + count == items.count))
                return (command, count)
            } catch RoomProtocolFailure.frameTooLarge where count > 1 { count /= 2 }
        }
    }

    private func acknowledge() async throws {
        guard let unresolved else { return }
        let result = try await client.execute(admission, command: unresolved)
        // Clear only after a matching authoritative result; cancellation/transport errors retain bytes.
        self.unresolved = nil
        minimumSequence = max(minimumSequence, result.sequence)
        if let error = result.errorCode { throw RoomProtocolFailure.rejected(error) }
        try Task.checkCancellation()
    }

    private func validatedSnapshot(_ initial: RoomBootstrap? = nil) async throws -> RoomBootstrap {
        let value: RoomBootstrap
        if let initial { value = initial } else { value = try await client.snapshot(admission) }
        guard value.snapshot.epoch == admission.bootstrap.snapshot.epoch,
            value.snapshot.sequence >= minimumSequence, value.connection == admission.bootstrap.connection,
            value.snapshot.members.contains(where: { $0.memberID == admission.memberID && $0.identity == identity })
        else { throw RoomProtocolFailure.malformed }
        return value
    }

    private func read(_ initial: RoomBootstrap?) async throws -> RoomCatalogView {
        let bootstrap = try await validatedSnapshot(initial)
        let client = client
        let admission = admission
        let view = try await withThrowingTaskGroup(of: RoomCatalogView.self) { group in
            group.addTask { try await client.catalogView(admission, bootstrap: bootstrap) }
            group.addTask {
                try await Task.sleep(for: .seconds(20))
                throw RoomProtocolFailure.transport
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
        try Task.checkCancellation()
        minimumSequence = view.bootstrap.snapshot.sequence
        return view
    }

    private func enter() async throws {
        if occupied { await withCheckedContinuation { waiters.append($0) } } else { occupied = true }
        do { try Task.checkCancellation() } catch {
            leave()
            throw error
        }
    }
    private func leave() {
        if waiters.isEmpty { occupied = false } else { waiters.removeFirst().resume() }
    }
}
