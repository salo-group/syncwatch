import CoreFoundation
import Foundation

public struct RoomCallStatus: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public var description: String { "[room call state redacted]" }
    public var debugDescription: String { description }
    public let state: String
    public let callID: String?
    public let generation: UInt64
    public let meetURI: String?
    public let reason: String?
    public let authorizationRequired: Bool
    public let pollAfterMs: UInt64
    public static func decode(_ bytes: Data) throws -> Self {
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(
            value, ["status", "callId", "generation", "meetUri", "reason", "authorizationRequired", "pollAfterMs"])
        let state = try RoomWire.string(value["status"])
        let id = try RoomWire.optionalString(value["callId"])
        let generation = try RoomWire.integer(value["generation"])
        let uri = try RoomWire.optionalString(value["meetUri"])
        let reason = try RoomWire.optionalString(value["reason"])
        let poll = try RoomWire.integer(value["pollAfterMs"], maximum: 10000)
        guard let permission = value["authorizationRequired"] as? NSNumber,
            CFGetTypeID(permission) == CFBooleanGetTypeID(), poll >= 1000,
            [
                "idle", "creating", "ready", "creationFailed", "creationUnknown", "ending", "ended", "endFailed",
                "endUnknown", "unavailable",
            ].contains(state),
            (id == nil) == (state == "idle"), (generation == 0) == (state == "idle"), (uri != nil) == (state == "ready")
        else { throw RoomProtocolFailure.malformed }
        if let id { _ = try AccountClient.token(id) }
        try RoomWire.validateMeetURI(uri)
        if ["creationFailed", "endFailed"].contains(state) {
            guard let reason,
                [
                    "permissionRequired", "providerBusy", "providerUnavailable", "providerResponseInvalid",
                    "providerPolicyInvalid", "roomChanged",
                ].contains(reason)
            else { throw RoomProtocolFailure.malformed }
        } else if ["creationUnknown", "endUnknown"].contains(state) {
            guard reason == "providerOutcomeUnknown" else { throw RoomProtocolFailure.malformed }
        } else if state == "unavailable" {
            guard reason == "roomChanged" else { throw RoomProtocolFailure.malformed }
        } else if reason != nil {
            throw RoomProtocolFailure.malformed
        }
        return Self(
            state: state, callID: id, generation: generation, meetURI: uri, reason: reason,
            authorizationRequired: permission.boolValue, pollAfterMs: poll)
    }
}
public struct RoomCallOperation: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    let requestID: String, roomID: String, memberID: String, epoch: String, action: String
    let callID: String?
    let generation: UInt64
    let scope: UUID
    public var description: String { "[room call operation redacted]" }
    public var debugDescription: String { description }
}
extension RoomClient {
    public func callStatus(_ admission: RoomAdmission) async throws -> RoomCallStatus {
        _ = try accountAuthority()
        return try RoomCallStatus.decode(
            await request("GET", "v2/rooms/\(admission.roomID)/call", admission: admission))
    }
    public func callStartOperation(_ admission: RoomAdmission) throws -> RoomCallOperation {
        try callOperation(admission, action: "start", status: nil)
    }
    public func callEndOperation(_ admission: RoomAdmission, status: RoomCallStatus) throws -> RoomCallOperation {
        guard status.callID != nil, status.generation > 0, ["ready", "endFailed"].contains(status.state) else {
            throw RoomProtocolFailure.malformed
        }
        return try callOperation(admission, action: "end", status: status)
    }
    private func callOperation(_ admission: RoomAdmission, action: String, status: RoomCallStatus?) throws
        -> RoomCallOperation
    {
        let authority = try accountAuthority()
        guard admission.accountScope == authority.scope else { throw RoomProtocolFailure.malformed }
        return RoomCallOperation(
            requestID: try AccountClient.secret(), roomID: admission.roomID, memberID: admission.memberID,
            epoch: admission.bootstrap.snapshot.epoch, action: action, callID: status?.callID,
            generation: status?.generation ?? 0, scope: authority.scope)
    }
    public func requestCall(_ operation: RoomCallOperation, admission: RoomAdmission) async throws -> RoomCallStatus {
        let authority = try accountAuthority()
        guard operation.scope == authority.scope, operation.roomID == admission.roomID,
            operation.memberID == admission.memberID,
            operation.epoch == admission.bootstrap.snapshot.epoch
        else { throw RoomProtocolFailure.malformed }
        var body: [String: Any] = ["v": 2, "requestId": operation.requestID]
        if let id = operation.callID {
            body["callId"] = id
            body["generation"] = operation.generation
        }
        return try RoomCallStatus.decode(
            await request(
                "POST", "v2/rooms/\(admission.roomID)/call/\(operation.action)",
                admission: admission, body: RoomWire.encode(body)))
    }
}
