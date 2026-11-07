import Foundation

struct AccountRoomCredential: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    let origin: URL
    let owner: UUID
    let accountID: String
    let access: String
    var description: String { "[room account credential redacted]" }
    var debugDescription: String { description }
}

/// One account-client lifetime. Pause before rotation; publish only after its secure checkpoint.
/// A room client borrows this authority and must not close its account owner's authority.
public actor RoomAccountAuthority {
    public nonisolated let address: RoomAddress
    public nonisolated let accountID: String
    nonisolated let scope = UUID()
    private let owner: UUID
    private enum Phase {
        case ready(AccountRoomCredential)
        case paused, unavailable, closed
    }
    private var phase: Phase
    private var waiters: [UUID: CheckedContinuation<AccountRoomCredential, Error>] = [:]
    var pendingRequests: Int { waiters.count }

    init(address: RoomAddress, device: AccountDeviceSession, owner: UUID) throws {
        let credential = try device.roomCredential()
        guard credential.origin == address.origin, credential.owner == owner else {
            throw RoomProtocolFailure.malformed
        }
        self.address = address
        accountID = credential.accountID
        self.owner = owner
        phase = .ready(credential)
    }
    public func pause() throws {
        if case .closed = phase { throw RoomProtocolFailure.closed }
        phase = .paused
    }
    public func commit(_ device: AccountDeviceSession) throws {
        if case .closed = phase { throw RoomProtocolFailure.closed }
        let credential = try device.roomCredential()
        guard credential.origin == address.origin, credential.owner == owner, credential.accountID == accountID else {
            throw RoomProtocolFailure.malformed
        }
        phase = .ready(credential)
        let pending = Array(waiters.values)
        waiters.removeAll()
        for continuation in pending { continuation.resume(returning: credential) }
    }
    public func unavailable() {
        if case .closed = phase { return }
        phase = .unavailable
        finish(RoomProtocolFailure.transport)
    }
    public func close() {
        phase = .closed
        finish(RoomProtocolFailure.closed)
    }
    private func finish(_ error: Error) {
        let pending = Array(waiters.values)
        waiters.removeAll()
        for continuation in pending { continuation.resume(throwing: error) }
    }
    func credential() async throws -> AccountRoomCredential {
        try Task.checkCancellation()
        let id = UUID()
        let result: AccountRoomCredential = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                switch phase {
                case .ready(let credential): continuation.resume(returning: credential)
                case .unavailable: continuation.resume(throwing: RoomProtocolFailure.transport)
                case .closed: continuation.resume(throwing: RoomProtocolFailure.closed)
                case .paused:
                    // Independent cancellations do not cancel another caller's rotation wait.
                    if waiters.count >= 64 {
                        continuation.resume(throwing: RoomProtocolFailure.rejected("queueFull"))
                    } else {
                        waiters[id] = continuation
                    }
                }
            }
        } onCancel: {
            Task { await self.cancel(id) }
        }
        try Task.checkCancellation()
        return result
    }
    private func cancel(_ id: UUID) { waiters.removeValue(forKey: id)?.resume(throwing: CancellationError()) }
}
