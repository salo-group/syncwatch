import Foundation
import XCTest

@testable import SyncWatchRoom

final class RoomMeetAuthorizationTests: XCTestCase, @unchecked Sendable {
    func testSharedStatusCorpus() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../../contracts/fixtures/meet-authorization.json").standardized
        let fixture = try RoomWire.object(Data(contentsOf: file))
        let account = try RoomWire.string(fixture["accountId"])
        for item in try XCTUnwrap(fixture["statuses"] as? [[String: Any]]) {
            let wire = Data(try RoomWire.string(item["wire"]).utf8)
            do {
                _ = try RoomMeetAuthorizationStatus.decode(wire, accountID: account)
                XCTAssertEqual(item["valid"] as? Bool, true, item["name"] as? String ?? "")
            } catch {
                XCTAssertTrue(error is RoomProtocolFailure)
                XCTAssertEqual(item["valid"] as? Bool, false, item["name"] as? String ?? "")
            }
        }
    }
}
