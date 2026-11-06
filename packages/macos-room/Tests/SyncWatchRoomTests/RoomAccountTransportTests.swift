import Foundation
import XCTest

@testable import SyncWatchRoom

final class RoomAccountTransportTests: XCTestCase, @unchecked Sendable {
    func testOwnedAuthenticatedService() async throws {
        guard let path = ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_ACCOUNT_FIXTURE"] else {
            throw XCTSkip("Requires an owned authenticated service with synthetic device sessions")
        }
        let fixture = try RoomWire.object(Data(contentsOf: URL(fileURLWithPath: path)))
        let ownerRecord = try AccountSavedSession.decode(RoomWire.encode(RoomWire.dictionary(fixture["owner"])))
        let guestRecord = try AccountSavedSession.decode(RoomWire.encode(RoomWire.dictionary(fixture["guest"])))
        let ownerAccount = AccountClient(address: try RoomAddress(ownerRecord.service))
        let guestAccount = AccountClient(address: try RoomAddress(guestRecord.service))
        var ownerRoom: RoomClient?
        var guestRoom: RoomClient?
        var ownerAuthority: RoomAccountAuthority?
        var guestAuthority: RoomAccountAuthority?
        do {
            let ownerDevice = try await ownerAccount.resume(ownerRecord)
            let guestDevice = try await guestAccount.resume(guestRecord)
            let owner = try ownerAccount.roomAuthority(ownerDevice)
            let guest = try guestAccount.roomAuthority(guestDevice)
            ownerAuthority = owner
            guestAuthority = guest
            let host = RoomClient(authority: owner)
            let peer = RoomClient(authority: guest)
            ownerRoom = host
            guestRoom = peer
            let run = "macos-" + UUID().uuidString
            let admission = try await host.create(runID: run, requestID: "create-" + UUID().uuidString)
            XCTAssertTrue(admission.isAccountBacked)
            let hostEvents = try await host.openEvents(admission)
            _ = try RoomBootstrap.initialEvent(await hostEvents.receive(), admission: admission)
            let invitation = try await host.invite(admission)
            let attempt = try await peer.requestAdmission(
                invitation, runID: "guest-" + UUID().uuidString, requestID: "join-" + UUID().uuidString)
            XCTAssertEqual(attempt.request.account, guestDevice.account)
            let pending = try await host.pending(admission)
            XCTAssertEqual(pending.map(\.id), [attempt.request.id])
            do {
                _ = try await peer.accept(attempt)
                XCTFail("Unapproved device admitted")
            } catch RoomProtocolFailure.rejected("admissionRequired") {}
            try await host.decide(admission, approvalID: attempt.request.id, approve: true)
            let approved = try await peer.admissionStatus(attempt)
            XCTAssertEqual(approved.status, "approved")
            let joined = try await peer.accept(attempt)
            let events = try await peer.openEvents(joined)
            let initial = try RoomBootstrap.initialEvent(await events.receive(), admission: joined)
            XCTAssertEqual(initial.snapshot.members.count, 2)
            let bound = try RoomWire.object(await hostEvents.receive())
            XCTAssertEqual(bound["type"] as? String, "member.bound")
            let current = try await host.snapshot(admission)
            XCTAssertEqual(current.snapshot.members.count, 2)
            let page = try await host.catalog(admission, bootstrap: current, cursor: current.catalogCursor)
            XCTAssertTrue(page.items.isEmpty)
            let first = try await peer.syncTicket(joined)
            let second = try await peer.syncTicket(joined)
            XCTAssertTrue(first.credential.value != second.credential.value, "Fresh transports need distinct tickets")
            let meet: RoomMeetAuthorization?
            if let token = fixture["browserFixtureToken"] as? String {
                meet = try await RoomMeetTransportChecks.run(
                    host: host, peer: peer, owner: admission, guest: joined,
                    account: ownerDevice.account, fixtureToken: token)
            } else {
                meet = nil
            }
            let withdrawal = try await peer.requestAdmission(
                host.invite(admission), runID: "withdraw-" + UUID().uuidString,
                requestID: "withdraw-" + UUID().uuidString)
            try await peer.cancelAdmission(withdrawal)
            try await peer.cancelAdmission(withdrawal)
            let withdrawn = try await peer.admissionStatus(withdrawal)
            XCTAssertEqual(withdrawn.status, "revoked")
            let race = try await peer.requestAdmission(
                host.invite(admission), runID: "race-" + UUID().uuidString, requestID: "race-" + UUID().uuidString)
            try await host.decide(admission, approvalID: race.request.id, approve: true)
            _ = try await peer.accept(race)
            try await peer.cancelAdmission(race)
            let afterCancellation = try await host.snapshot(admission)
            XCTAssertEqual(afterCancellation.snapshot.members.count, 2)
            let revoked = try await peer.admissionStatus(race)
            XCTAssertEqual(revoked.status, "revoked")
            try await owner.pause()
            let checkpoint = try await ownerAccount.checkpoint(ownerDevice)
            let rotated = try await ownerAccount.resume(checkpoint)
            _ = try await ownerAccount.checkpoint(rotated)
            try await owner.commit(rotated)
            _ = try await host.snapshot(admission)
            try await guestAccount.logout(guestDevice)
            do {
                _ = try await peer.snapshot(joined)
                XCTFail("Logged-out device retained access")
            } catch RoomProtocolFailure.rejected("unauthorized") {}
            try await host.leave(admission)
            if let meet {
                let cancelled = try await host.cancelMeetAuthorization(meet, admission: admission)
                XCTAssertEqual(cancelled.state, "cancelled")
            }
            try await ownerAccount.logout(rotated)
        } catch {
            await ownerRoom?.close()
            await guestRoom?.close()
            await ownerAuthority?.close()
            await guestAuthority?.close()
            await ownerAccount.close()
            await guestAccount.close()
            throw error
        }
        await ownerRoom?.close()
        await guestRoom?.close()
        await ownerAuthority?.close()
        await guestAuthority?.close()
        await ownerAccount.close()
        await guestAccount.close()
    }
}
