import Foundation
import XCTest

@testable import SyncWatchRoom

final class RoomAccountAuthorityTests: XCTestCase, @unchecked Sendable {
    func testRotationWaitsForCheckpointAndCancellationIsIndependent() async throws {
        let address = try RoomAddress("https://service.example.invalid/")
        let owner = UUID()
        let profile = AccountProfile(
            id: String(repeating: "A", count: 43), name: "Owner", email: "owner@example.invalid")
        func device(_ value: String, issuer: URL? = nil, credentialOwner: UUID? = nil) -> AccountDeviceSession {
            AccountDeviceSession(
                account: profile, access: String(repeating: value, count: 43),
                refresh: String(repeating: "R", count: 43), issuer: issuer ?? address.origin,
                credentialOwner: credentialOwner ?? owner)
        }
        let authority = try RoomAccountAuthority(address: address, device: device("B"), owner: owner)
        func pending(_ expected: Int) async {
            let clock = ContinuousClock()
            let deadline = clock.now.advanced(by: .seconds(3))
            while (await authority.pendingRequests) != expected && clock.now < deadline { await Task.yield() }
            let count = await authority.pendingRequests
            XCTAssertEqual(count, expected)
        }
        let first = try await authority.credential()
        XCTAssertFalse(String(describing: first).contains(first.access))
        XCTAssertFalse(String(reflecting: first).contains(first.access))
        try await authority.pause()
        let cancelled = Task { try await authority.credential() }
        let survivor = Task { try await authority.credential() }
        await pending(2)
        cancelled.cancel()
        try await authority.commit(device("C"))
        do {
            _ = try await cancelled.value
            XCTFail("Cancelled room request acquired a credential")
        } catch is CancellationError {}
        let rotated = try await survivor.value
        XCTAssertEqual(rotated.access, String(repeating: "C", count: 43))
        do {
            try await authority.commit(device("D", issuer: URL(string: "https://other.example.invalid/")!))
            XCTFail("Origin mix")
        } catch RoomProtocolFailure.malformed {}
        do {
            try await authority.commit(device("D", credentialOwner: UUID()))
            XCTFail("Account-client mix")
        } catch RoomProtocolFailure.malformed {}
        let other = AccountDeviceSession(
            account: AccountProfile(
                id: String(repeating: "Z", count: 43), name: "Other", email: "other@example.invalid"),
            access: String(repeating: "D", count: 43), refresh: String(repeating: "R", count: 43),
            issuer: address.origin, credentialOwner: owner)
        do {
            try await authority.commit(other)
            XCTFail("Account mix")
        } catch RoomProtocolFailure.malformed {}
        let retained = try await authority.credential()
        XCTAssertEqual(retained.access, rotated.access)
        try await authority.pause()
        let failed = Task { try await authority.credential() }
        await pending(1)
        await authority.unavailable()
        do {
            _ = try await failed.value
            XCTFail("Unavailable account authorized a room")
        } catch RoomProtocolFailure.transport {}
        try await authority.pause()
        let restored = Task { try await authority.credential() }
        await pending(1)
        try await authority.commit(device("E"))
        let recovered = try await restored.value
        XCTAssertEqual(recovered.access, String(repeating: "E", count: 43))
        try await authority.pause()
        let closing = Task { try await authority.credential() }
        await pending(1)
        await authority.close()
        await pending(0)
        do {
            _ = try await closing.value
            XCTFail("Closed authority authorized a room")
        } catch RoomProtocolFailure.closed {}
        do {
            try await authority.commit(device("F"))
            XCTFail("Closed authority revived")
        } catch RoomProtocolFailure.closed {}
        await authority.close()
    }
}
