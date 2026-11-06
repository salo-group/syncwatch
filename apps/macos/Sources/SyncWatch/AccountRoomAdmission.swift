import Foundation
import SyncWatchRoom

enum RoomAdmissionFailure: Error {
    case denied, expired
    var notice: String { self == .denied ? "roomAdmissionDenied" : "roomAdmissionExpired" }
}

/// One correlated attempt; transport retries retain the same IDs and never restart Google sign-in.
@MainActor final class AccountRoomAdmission {
    private let client: RoomClient
    private let owner: Bool
    private let invitation: RoomInvitation?
    private let runID = "run-" + UUID().uuidString
    private let requestID = "request-" + UUID().uuidString
    private var attempt: RoomAdmissionAttempt?
    init(client: RoomClient, owner: Bool, values: [String: String]) throws {
        self.client = client
        self.owner = owner
        invitation =
            owner
            ? nil
            : try RoomInvitation(
                roomID: values["roomID"] ?? "", credential: RoomSecret(values["invitation"] ?? ""))
    }
    func perform(waiting: @escaping @MainActor @Sendable () -> Void) async throws -> RoomAdmission {
        try await withThrowingTaskGroup(of: RoomAdmission.self) { group in
            group.addTask { try await self.admit(waiting: waiting) }
            group.addTask {
                try await Task.sleep(for: .seconds(120))
                throw RoomAdmissionFailure.expired
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
    }
    private func admit(waiting: @MainActor () -> Void) async throws -> RoomAdmission {
        if owner {
            return try await retry { try await self.client.create(runID: self.runID, requestID: self.requestID) }
        }
        guard let invitation else { throw RoomProtocolFailure.malformed }
        let request = try await retry {
            try await self.client.requestAdmission(invitation, runID: self.runID, requestID: self.requestID)
        }
        attempt = request
        try Task.checkCancellation()
        waiting()
        var status = request.request.status
        while true {
            switch status {
            case "approved", "admitted": return try await retry { try await self.client.accept(request) }
            case "pending": break
            case "denied": throw RoomAdmissionFailure.denied
            default: throw RoomAdmissionFailure.expired
            }
            try await Task.sleep(for: .seconds(3))
            status = try await retry { try await self.client.admissionStatus(request) }.status
        }
    }
    func cancel() async throws {
        if let attempt { try await client.cancelAdmission(attempt) }
    }
    private func retry<T: Sendable>(_ operation: @MainActor () async throws -> T) async throws -> T {
        for index in 0..<2 {
            do { return try await operation() } catch {
                try Task.checkCancellation()
                let transient: Bool
                if case RoomProtocolFailure.transport = error {
                    transient = true
                } else if let error = error as? URLError {
                    transient = [
                        .timedOut, .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet,
                        .cannotFindHost, .dnsLookupFailed,
                    ].contains(error.code)
                } else {
                    transient = false
                }
                guard index == 0, transient else { throw error }
                try await Task.sleep(for: .seconds(1))
            }
        }
        throw RoomProtocolFailure.transport
    }
}
