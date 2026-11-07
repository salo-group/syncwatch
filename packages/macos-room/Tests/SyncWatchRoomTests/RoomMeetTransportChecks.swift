import Foundation
import XCTest

@testable import SyncWatchRoom

enum RoomMeetTransportChecks {
    static func run(
        host: RoomClient, peer: RoomClient, owner: RoomAdmission, guest: RoomAdmission,
        account: AccountProfile, fixtureToken: String
    ) async throws -> RoomMeetAuthorization {
        func browser(_ flow: RoomMeetAuthorization, _ scenario: String) async throws -> String? {
            let body = try RoomWire.encode([
                "v": 2, "transactionId": flow.id, "scenario": scenario, "fixtureToken": fixtureToken,
            ])
            let response = try RoomWire.object(await host.request("POST", "__test/meet-browser", body: body))
            try RoomWire.keys(response, ["browserProof"])
            return try RoomWire.optionalString(response["browserProof"])
        }
        do {
            _ = try await peer.beginMeetAuthorization(guest)
            XCTFail("Guest requested organizer permission")
        } catch RoomProtocolFailure.rejected("forbidden") {}
        let call = try await host.callStartOperation(owner)
        let unsignedCall = try await host.requestCall(call, admission: owner)
        XCTAssertEqual(unsignedCall.state, "idle")
        XCTAssertTrue(unsignedCall.authorizationRequired)
        let mismatch = try await host.beginMeetAuthorization(owner)
        let pending = try await host.meetAuthorizationStatus(mismatch, admission: owner)
        XCTAssertEqual(pending.state, "pending")
        let wrongAccountCode = try await browser(mismatch, "wrongAccount")
        XCTAssertNil(wrongAccountCode)
        let failed = try await host.meetAuthorizationStatus(mismatch, admission: owner)
        XCTAssertEqual(failed.state, "failed")
        XCTAssertEqual(failed.reason, "accountMismatch")

        let refused = try await host.beginMeetAuthorization(owner)
        let refusedCode = try await browser(refused, "missingScope")
        XCTAssertNil(refusedCode)
        let permission = try await host.meetAuthorizationStatus(refused, admission: owner)
        XCTAssertEqual(permission.reason, "permissionUnavailable")

        let flow = try await host.beginMeetAuthorization(owner)
        XCTAssertFalse(String(describing: flow).contains(flow.proof))
        do {
            _ = try await peer.meetAuthorizationStatus(flow, admission: guest)
            XCTFail("Foreign flow reached transport")
        } catch RoomProtocolFailure.malformed {}
        let result = try await browser(flow, "accept")
        let code = try XCTUnwrap(result)
        let waiting = try await host.meetAuthorizationStatus(flow, admission: owner)
        XCTAssertEqual(waiting.state, "browserConfirmationRequired")
        XCTAssertNil(waiting.account)
        let wrong = (code.first == "A" ? "B" : "A") + String(code.dropFirst())
        let invalid = try await host.verifyMeetAuthorization(flow, admission: owner, code: wrong)
        XCTAssertEqual(invalid.reason, "invalidConfirmation")
        XCTAssertNil(invalid.account)
        let verified = try await host.verifyMeetAuthorization(flow, admission: owner, code: code)
        XCTAssertEqual(verified.state, "confirmAuthorization")
        XCTAssertEqual(verified.account, account)
        for _ in 0..<2 {
            let complete = try await host.confirmMeetAuthorization(flow, admission: owner, code: code)
            XCTAssertEqual(complete.state, "complete")
        }
        let retained = try await host.cancelMeetAuthorization(flow, admission: owner)
        XCTAssertEqual(retained.state, "complete")
        _ = try await host.requestCall(call, admission: owner)
        let clock = ContinuousClock()
        let deadline = ContinuousClock().now.advanced(by: .seconds(5))
        var ready = try await host.callStatus(owner)
        while ready.state == "creating", clock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
            ready = try await host.callStatus(owner)
        }
        XCTAssertEqual(ready.state, "ready")
        XCTAssertNotNil(ready.meetURI)
        let retry = try await host.requestCall(call, admission: owner)
        XCTAssertEqual(retry.callID, ready.callID)
        XCTAssertEqual(retry.generation, ready.generation)
        let guestCall = try await peer.callStatus(guest)
        XCTAssertEqual(guestCall.meetURI, ready.meetURI)
        XCTAssertFalse(guestCall.authorizationRequired)
        let forbiddenEnd = try await peer.callEndOperation(guest, status: guestCall)
        do {
            _ = try await peer.requestCall(forbiddenEnd, admission: guest)
            XCTFail("Guest ended owner call")
        } catch RoomProtocolFailure.rejected("forbidden") {}
        let end = try await host.callEndOperation(owner, status: ready)
        _ = try await host.requestCall(end, admission: owner)
        var ended = try await host.callStatus(owner)
        while ended.state == "ending", clock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
            ended = try await host.callStatus(owner)
        }
        XCTAssertEqual(ended.state, "ended")
        XCTAssertNil(ended.meetURI)
        let endRetry = try await host.requestCall(end, admission: owner)
        XCTAssertEqual(endRetry.state, "ended")
        print(
            "SYNCWATCH_ROOM_CALL_TRANSPORT_OK synthetic provider, current membership, stable create/end, guest denial and URI fencing"
        )
        let reuse = try await host.beginMeetAuthorization(owner)
        let reusedCode = try await browser(reuse, "reuse")
        XCTAssertNotNil(reusedCode)
        for _ in 0..<2 {
            let cancelled = try await host.cancelMeetAuthorization(reuse, admission: owner)
            XCTAssertEqual(cancelled.state, "cancelled")
        }
        print(
            "SYNCWATCH_MEET_AUTHORIZATION_TRANSPORT_OK synthetic provider, OAuth scope/PKCE, counterproof, owner scope, retry and cancellation"
        )
        return try await host.beginMeetAuthorization(owner)
    }
}
