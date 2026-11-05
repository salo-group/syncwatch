import Foundation

public struct RoomMeetAuthorization: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let browserURI: URL
    public let expiresIn: UInt64
    public let pollAfterMs: UInt64
    let id: String, proof: String, confirmation: String
    let roomID: String, memberID: String, epoch: String
    let scope: UUID
    public var description: String { "[Meet authorization redacted]" }
    public var debugDescription: String { description }
}
public struct RoomMeetAuthorizationStatus: Sendable {
    public let state: String
    public let account: AccountProfile?
    public let reason: String?
    public static func decode(_ bytes: Data, accountID: String) throws -> Self {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["status", "account", "reason"])
        let state = try RoomWire.string(value["status"])
        let reason = try RoomWire.optionalString(value["reason"])
        let account: AccountProfile?
        if value["account"] is NSNull { account = nil }
        else {
            let profile = try RoomWire.dictionary(value["account"])
            try RoomWire.keys(profile, ["id", "name", "email"])
            account = try AccountClient.profile(profile)
        }
        let states = ["pending", "authorizing", "browserConfirmationRequired", "confirmAuthorization", "complete", "cancelled", "failed", "expired"]
        guard states.contains(state), (account != nil) == (state == "confirmAuthorization"),
            account.map({ $0.id == accountID }) ?? true else { throw RoomProtocolFailure.malformed }
        if state == "failed" {
            guard let reason, ["roomChanged", "accountMismatch", "permissionUnavailable", "invalidConfirmation"].contains(reason) else { throw RoomProtocolFailure.malformed }
        } else if ["browserConfirmationRequired", "confirmAuthorization"].contains(state) {
            guard reason == nil || reason == "invalidConfirmation" else { throw RoomProtocolFailure.malformed }
        } else if reason != nil { throw RoomProtocolFailure.malformed }
        return Self(state: state, account: account, reason: reason)
    }
}

extension RoomClient {
    public func beginMeetAuthorization(_ admission: RoomAdmission) async throws -> RoomMeetAuthorization {
        let authority = try accountAuthority(), proof = try AccountClient.secret()
        let body = try RoomWire.encode(["v": 2, "challenge": AccountClient.challenge(proof)])
        let value = try RoomWire.object(await request("POST", "v2/rooms/\(admission.roomID)/call-authorizations", admission: admission, body: body))
        try RoomWire.keys(value, ["transactionId", "browserUri", "expiresIn", "pollAfterMs"])
        let id = try AccountClient.token(value["transactionId"])
        let uri = URL(string: address.endpoint("auth/google/meet-start").absoluteString + "?id=" + id)!
        let expires = try RoomWire.integer(value["expiresIn"], maximum: 600)
        let poll = try RoomWire.integer(value["pollAfterMs"], maximum: 10000)
        guard value["browserUri"] as? String == uri.absoluteString, expires > 0, poll >= 1000 else { throw RoomProtocolFailure.malformed }
        return RoomMeetAuthorization(browserURI: uri, expiresIn: expires, pollAfterMs: poll, id: id, proof: proof,
            confirmation: try AccountClient.secret(), roomID: admission.roomID, memberID: admission.memberID,
            epoch: admission.bootstrap.snapshot.epoch, scope: authority.scope)
    }
    public func meetAuthorizationStatus(_ flow: RoomMeetAuthorization, admission: RoomAdmission) async throws -> RoomMeetAuthorizationStatus {
        try await meetAuthorization("poll", flow: flow, admission: admission)
    }
    public func verifyMeetAuthorization(_ flow: RoomMeetAuthorization, admission: RoomAdmission, code: String) async throws -> RoomMeetAuthorizationStatus {
        try await meetAuthorization("verify", flow: flow, admission: admission, code: code)
    }
    public func confirmMeetAuthorization(_ flow: RoomMeetAuthorization, admission: RoomAdmission, code: String) async throws -> RoomMeetAuthorizationStatus {
        try await meetAuthorization("confirm", flow: flow, admission: admission, code: code)
    }
    public func cancelMeetAuthorization(_ flow: RoomMeetAuthorization, admission: RoomAdmission) async throws -> RoomMeetAuthorizationStatus {
        try await meetAuthorization("cancel", flow: flow, admission: admission)
    }
    private func meetAuthorization(_ action: String, flow: RoomMeetAuthorization, admission: RoomAdmission, code: String? = nil) async throws -> RoomMeetAuthorizationStatus {
        let authority = try accountAuthority()
        guard flow.scope == authority.scope, flow.roomID == admission.roomID, flow.memberID == admission.memberID,
            flow.epoch == admission.bootstrap.snapshot.epoch else { throw RoomProtocolFailure.malformed }
        var body: [String: Any] = ["v": 2, "proof": flow.proof]
        if let code {
            guard code.range(of: "\\A[0-9A-F]{12}\\z", options: .regularExpression) != nil else { throw RoomProtocolFailure.malformed }
            body["browserProof"] = code
        }
        if action == "confirm" { body["requestId"] = flow.confirmation }
        return try RoomMeetAuthorizationStatus.decode(await request("POST", "v2/call-authorizations/\(flow.id)/\(action)",
            admission: admission, body: RoomWire.encode(body)), accountID: authority.accountID)
    }
}
