import Foundation

public struct RoomAdmissionRequest: Sendable {
    public let id: String
    public let status: String
    public let account: AccountProfile
    static func decode(_ input: Any?) throws -> Self {
        let value = try RoomWire.dictionary(input)
        try RoomWire.keys(value, ["id", "status", "accountId", "name", "email"])
        let id = try RoomWire.string(value["id"])
        try RoomWire.id(id)
        let status = try RoomWire.string(value["status"])
        guard ["pending", "approved", "denied", "admitted", "expired", "revoked", "closed"].contains(status) else {
            throw RoomProtocolFailure.malformed
        }
        let account = AccountProfile(
            id: try RoomWire.string(value["accountId"]), name: try RoomWire.string(value["name"]),
            email: try RoomWire.string(value["email"]))
        guard account.id.range(of: "\\A[A-Za-z0-9_-]{43}\\z", options: .regularExpression) != nil else {
            throw RoomProtocolFailure.malformed
        }
        try RoomWire.text(account.name, maximum: 128)
        try RoomWire.text(account.email, maximum: 320)
        return Self(id: id, status: status, account: account)
    }
}

/// Correlation and authority stay together; an owner's approval ID is a different identifier.
public struct RoomAdmissionAttempt: Sendable {
    public let roomID: String
    public let request: RoomAdmissionRequest
    let requestID: String
    let runID: String
    let scope: UUID
}

public struct RoomSyncTicket: Sendable {
    public let credential: RoomSecret
    public let expiresIn: UInt64
    public func admissionCredential() -> String { credential.value }
}

extension RoomAdmission {
    static func account(_ bytes: Data, runID: String, scope: UUID, expectedRoom: String? = nil) throws -> Self {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["roomId", "memberId", "expiresAt", "bootstrap"])
        let room = try RoomWire.string(value["roomId"])
        let member = try RoomWire.string(value["memberId"])
        try RoomWire.id(room)
        try RoomWire.id(member)
        let bootstrap = try RoomWire.bootstrap(value["bootstrap"])
        guard expectedRoom.map({ $0 == room }) ?? true,
            bootstrap.snapshot.members.first(where: { $0.memberID == member })?.identity.clientRunID == runID
        else { throw RoomProtocolFailure.malformed }
        let time = try RoomWire.string(value["expiresAt"])
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fractional = formatter.date(from: time)
        formatter.formatOptions = [.withInternetDateTime]
        guard time.utf8.count <= 64, let expires = fractional ?? formatter.date(from: time) else {
            throw RoomProtocolFailure.malformed
        }
        var result = Self(
            roomID: room, memberID: member, token: nil, invitation: nil, expiresAt: expires, bootstrap: bootstrap)
        result.accountScope = scope
        return result
    }
}

enum RoomAccessWire {
    static func result(_ bytes: Data) throws -> Data {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["v", "result"])
        guard try RoomWire.integer(value["v"], maximum: 2) == 2, let result = value["result"],
            result is [String: Any] || result is [Any]
        else {
            throw RoomProtocolFailure.malformed
        }
        return try RoomWire.encode(result)
    }
    static func status(_ bytes: Data, _ expected: String) throws {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["status"])
        guard value["status"] as? String == expected else { throw RoomProtocolFailure.malformed }
    }
    static func secret(_ input: Any?) throws -> RoomSecret {
        let value = try RoomWire.string(input)
        guard value.range(of: "\\A[0-9A-F]{64}\\z", options: .regularExpression) != nil else {
            throw RoomProtocolFailure.malformed
        }
        return try RoomSecret(value)
    }
}
