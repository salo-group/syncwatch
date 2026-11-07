import Foundation
import XCTest

@testable import SyncWatchRoom

final class AccountSessionStoreTests: XCTestCase, @unchecked Sendable {
    private func records() throws -> [[String: Any]] {
        var root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<4 { root.deleteLastPathComponent() }
        let bytes = try Data(contentsOf: root.appendingPathComponent("contracts/fixtures/account-persistence.json"))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        return try XCTUnwrap(object["cases"] as? [[String: Any]])
    }
    func testCommonCheckpointCorpusAndRedaction() throws {
        for entry in try records() {
            let wire = try XCTUnwrap(entry["wire"] as? String)
            let expected = try XCTUnwrap(entry["valid"] as? Bool)
            let record = try? AccountSavedSession.decode(Data(wire.utf8))
            XCTAssertEqual(record != nil, expected, entry["name"] as? String ?? "checkpoint")
            if let record {
                XCTAssertFalse(record.description.contains(String(repeating: "B", count: 43)))
                XCTAssertEqual(try AccountSavedSession.decode(record.encode()).requestID, record.requestID)
            }
        }
    }
    func testProfileLeaseCheckpointRestartAndRemoval() async throws {
        guard ProcessInfo.processInfo.environment["SYNCWATCH_ACCOUNT_VAULT_CHECKS"] == "1" else {
            throw XCTSkip("Requires explicit owned native keychain qualification")
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
            "sw-account-session-" + UUID().uuidString)
        let first = AccountSessionStore(directory: directory)
        let second = AccountSessionStore(directory: directory)
        let record = try AccountSavedSession.decode(Data(try XCTUnwrap(records().first?["wire"] as? String).utf8))
        do {
            let initial = try await first.read()
            XCTAssertNil(initial)
            try await first.save(record)
            var denied = false
            do { _ = try await second.read() } catch { denied = true }
            XCTAssertTrue(denied)
            await first.close()
            await second.close()
            let restored = AccountSessionStore(directory: directory)
            do {
                let value = try await restored.read()
                XCTAssertEqual(value?.requestID, record.requestID)
                XCTAssertEqual(value?.refreshToken, record.refreshToken)
                try await restored.forget()
                let removed = try await restored.read()
                XCTAssertNil(removed)
                await restored.close()
            } catch {
                await restored.close()
                throw error
            }
        } catch {
            await first.close()
            await second.close()
            let cleanup = AccountSessionStore(directory: directory)
            try? await cleanup.forget()
            await cleanup.close()
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
        let cleanup = AccountSessionStore(directory: directory)
        try await cleanup.forget()
        await cleanup.close()
        try FileManager.default.removeItem(at: directory)
    }
}
