import Foundation
import XCTest

@testable import SyncWatchRoom

final class RoomAccountPortTests: XCTestCase, @unchecked Sendable {
    func testExplicitAdmissionRejectsLegacyDowngradeAndWrongRun() throws {
        let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent(
            "../../../../contracts/fixtures/room-client.json").standardized
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fixture)) as? [String: Any])
        let bootstrap = try RoomWire.object(Data(XCTUnwrap(root["catalogBootstrap"] as? String).utf8))
        let payload: [String: Any] = ["roomId": "room-test", "memberId": "member-owner",
            "expiresAt": "2030-01-01T00:00:00Z", "bootstrap": bootstrap]
        let bytes = try RoomWire.encode(payload)
        let scope = UUID()
        let admission = try RoomAdmission.account(bytes, runID: "run-room-client", scope: scope)
        XCTAssertNil(admission.token)
        XCTAssertNil(admission.invitation)
        XCTAssertEqual(admission.accountScope, scope)
        XCTAssertTrue(admission.isAccountBacked)
        XCTAssertThrowsError(try RoomAdmission.account(bytes, runID: "other-run", scope: scope))
        XCTAssertThrowsError(try RoomAdmission.account(bytes, runID: "run-room-client", scope: scope, expectedRoom: "other-room"))
        var legacy = payload
        legacy["token"] = String(repeating: "A", count: 64)
        XCTAssertThrowsError(try RoomAdmission.account(RoomWire.encode(legacy), runID: "run-room-client", scope: scope))
        XCTAssertThrowsError(try RoomAccessWire.result(RoomWire.encode(["v": 1, "result": payload])))
        XCTAssertThrowsError(try RoomAccessWire.result(Data("{\"v\":2,\"v\":2,\"result\":{}}".utf8)))
        let array = try RoomAccessWire.result(RoomWire.encode(["v": 2, "result": []]))
        XCTAssertEqual((try RoomWire.value(array) as? [Any])?.count, 0)
    }

    func testCloseCancelsPausedHttpAndEventAuthorizationWithoutClosingAccount() async throws {
        let address = try RoomAddress("http://127.0.0.1:1/")
        let owner = UUID()
        let device = AccountDeviceSession(
            account: AccountProfile(id: String(repeating: "A", count: 43), name: "Owner", email: "owner@example.invalid"),
            access: String(repeating: "B", count: 43), refresh: String(repeating: "R", count: 43), issuer: address.origin, credentialOwner: owner)
        let authority = try RoomAccountAuthority(address: address, device: device, owner: owner)
        let client = RoomClient(authority: authority)
        let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent(
            "../../../../contracts/fixtures/room-client.json").standardized
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fixture)) as? [String: Any])
        let bootstrap = try RoomWire.object(Data(XCTUnwrap(root["catalogBootstrap"] as? String).utf8))
        let payload = try RoomWire.encode(["roomId": "room-test", "memberId": "member-owner", "expiresAt": "2030-01-01T00:00:00Z", "bootstrap": bootstrap])
        let foreign = try RoomAdmission.account(payload, runID: "run-room-client", scope: UUID())
        do { _ = try await client.snapshot(foreign); XCTFail("Foreign authority reached transport") }
        catch RoomProtocolFailure.malformed {}
        do { _ = try await client.beginMeetAuthorization(foreign); XCTFail("Foreign Meet authority reached transport") }
        catch RoomProtocolFailure.malformed {}
        do { _ = try await client.callStartOperation(foreign); XCTFail("Foreign call authority accepted") }
        catch RoomProtocolFailure.malformed {}
        let admission = try RoomAdmission.account(payload, runID: "run-room-client", scope: authority.scope)
        let operation = try await client.callStartOperation(admission)
        XCTAssertFalse(String(describing: operation).contains(operation.requestID))
        try await authority.pause()
        let request = Task { try await client.create(runID: "run-new", requestID: "create-new") }
        let events = Task { try await client.openEvents(admission) }
        let permission = Task { try await client.beginMeetAuthorization(admission) }
        let call = Task { try await client.requestCall(operation, admission: admission) }
        let status = Task { try await client.callStatus(admission) }
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(3))
        while await authority.pendingRequests != 5 && clock.now < deadline { await Task.yield() }
        let count = await authority.pendingRequests
        XCTAssertEqual(count, 5)
        await client.close()
        for result in [await request.result.map { _ in () }, await events.result.map { _ in () }, await permission.result.map { _ in () },
            await call.result.map { _ in () }, await status.result.map { _ in () }] {
            if case .success = result { XCTFail("Closed client completed pending authorization") }
        }
        let remaining = await authority.pendingRequests
        XCTAssertEqual(remaining, 0)
        try await authority.commit(device)
        let retained = try await authority.credential()
        XCTAssertEqual(retained.accountID, device.account.id)
        await authority.close()
    }
}
