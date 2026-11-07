import Foundation
import XCTest

@testable import SyncWatchRoom

final class RoomCallTests: XCTestCase, @unchecked Sendable {
    func testSharedStatusCorpus() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../../contracts/fixtures/room-call.json").standardized
        let fixture = try RoomWire.object(Data(contentsOf: file))
        for item in try XCTUnwrap(fixture["statuses"] as? [[String: Any]]) {
            let wire = Data(try RoomWire.string(item["wire"]).utf8)
            do {
                _ = try RoomCallStatus.decode(wire)
                XCTAssertEqual(item["valid"] as? Bool, true, item["name"] as? String ?? "")
            } catch {
                XCTAssertTrue(error is RoomProtocolFailure)
                XCTAssertEqual(item["valid"] as? Bool, false, item["name"] as? String ?? "")
            }
        }
    }
}
