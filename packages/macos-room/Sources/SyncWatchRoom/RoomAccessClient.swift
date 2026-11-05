import Foundation

extension RoomClient {
    func accountAuthority() throws -> RoomAccountAuthority {
        guard let authority else { throw RoomProtocolFailure.malformed }
        return authority
    }
    public func create(runID: String, requestID: String) async throws -> RoomAdmission {
        let authority = try accountAuthority()
        try RoomWire.id(runID)
        try RoomWire.id(requestID)
        let body = try RoomWire.encode(["v": 2, "clientRunId": runID, "requestId": requestID])
        return try RoomAdmission.account(
            await request("POST", "v2/rooms", body: body), runID: runID, scope: authority.scope)
    }
    public func invite(_ admission: RoomAdmission) async throws -> RoomInvitation {
        _ = try accountAuthority()
        let value = try RoomWire.object(await request("POST", "v2/rooms/\(admission.roomID)/invitation", admission: admission))
        try RoomWire.keys(value, ["roomId", "invitation", "expiresIn"])
        guard value["roomId"] as? String == admission.roomID,
            try RoomWire.integer(value["expiresIn"], maximum: 86400) > 0 else { throw RoomProtocolFailure.malformed }
        return try RoomInvitation(roomID: admission.roomID, credential: RoomAccessWire.secret(value["invitation"]))
    }
    public func requestAdmission(_ invitation: RoomInvitation, runID: String, requestID: String) async throws -> RoomAdmissionAttempt {
        let authority = try accountAuthority()
        try RoomWire.id(runID)
        try RoomWire.id(requestID)
        _ = try RoomAccessWire.secret(invitation.credential.value)
        let body = try RoomWire.encode([
            "v": 2, "clientRunId": runID, "requestId": requestID, "invitation": invitation.credential.value])
        let request = try RoomAdmissionRequest.decode(RoomWire.object(
            await self.request("POST", "v2/rooms/\(invitation.roomID)/admission-requests", body: body)))
        guard request.account.id == authority.accountID else { throw RoomProtocolFailure.malformed }
        return RoomAdmissionAttempt(roomID: invitation.roomID, request: request, requestID: requestID, runID: runID, scope: authority.scope)
    }
    private func validate(_ attempt: RoomAdmissionAttempt) throws -> RoomAccountAuthority {
        let authority = try accountAuthority()
        guard attempt.scope == authority.scope else { throw RoomProtocolFailure.malformed }
        return authority
    }
    public func admissionStatus(_ attempt: RoomAdmissionAttempt) async throws -> RoomAdmissionRequest {
        let authority = try validate(attempt)
        let value = try RoomAdmissionRequest.decode(RoomWire.object(
            await request("GET", "v2/room-admissions/\(attempt.requestID)", runID: attempt.runID)))
        guard value.id == attempt.request.id, value.account.id == authority.accountID else { throw RoomProtocolFailure.malformed }
        return value
    }
    public func accept(_ attempt: RoomAdmissionAttempt) async throws -> RoomAdmission {
        let authority = try validate(attempt)
        let body = try RoomWire.encode(["v": 2, "clientRunId": attempt.runID])
        return try RoomAdmission.account(
            await request("POST", "v2/room-admissions/\(attempt.requestID)/accept", body: body),
            runID: attempt.runID, scope: authority.scope, expectedRoom: attempt.roomID)
    }
    public func cancelAdmission(_ attempt: RoomAdmissionAttempt) async throws {
        _ = try validate(attempt)
        let body = try RoomWire.encode(["v": 2, "clientRunId": attempt.runID])
        try RoomAccessWire.status(await request("POST", "v2/room-admissions/\(attempt.requestID)/cancel", body: body), "revoked")
    }
    public func pending(_ admission: RoomAdmission) async throws -> [RoomAdmissionRequest] {
        _ = try accountAuthority()
        let bytes = try await request("GET", "v2/rooms/\(admission.roomID)/pending", admission: admission)
        guard let items = try RoomWire.value(bytes) as? [Any], items.count <= 64 else { throw RoomProtocolFailure.malformed }
        let values = try items.map(RoomAdmissionRequest.decode)
        guard Set(values.map(\.id)).count == values.count, values.allSatisfy({ $0.status == "pending" }) else {
            throw RoomProtocolFailure.malformed
        }
        return values
    }
    public func decide(_ admission: RoomAdmission, approvalID: String, approve: Bool) async throws {
        _ = try accountAuthority()
        try RoomWire.id(approvalID)
        let body = try RoomWire.encode(["v": 2, "approvalId": approvalID, "approve": approve])
        try RoomAccessWire.status(await request("POST", "v2/rooms/\(admission.roomID)/decision", admission: admission, body: body), "decided")
    }
    public func revoke(_ admission: RoomAdmission, memberID: String) async throws {
        _ = try accountAuthority()
        try RoomWire.id(memberID)
        let body = try RoomWire.encode(["v": 2, "memberId": memberID])
        try RoomAccessWire.status(await request("POST", "v2/rooms/\(admission.roomID)/revoke", admission: admission, body: body), "revoked")
    }
    public func syncTicket(_ admission: RoomAdmission) async throws -> RoomSyncTicket {
        _ = try accountAuthority()
        let value = try RoomWire.object(await request("POST", "v2/rooms/\(admission.roomID)/sync-ticket", admission: admission))
        try RoomWire.keys(value, ["ticket", "expiresIn"])
        let seconds = try RoomWire.integer(value["expiresIn"], maximum: 60)
        guard seconds > 0 else { throw RoomProtocolFailure.malformed }
        return try RoomSyncTicket(credential: RoomAccessWire.secret(value["ticket"]), expiresIn: seconds)
    }
}
