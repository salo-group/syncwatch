import Foundation
import XCTest

@testable import SyncWatchRoom

final class AccountCredentialVaultTests: XCTestCase {
    func testInvalidScopeAndOversizedRecordsAreRejectedBeforeOSAccess() throws {
        for scope in [
            "", String(repeating: "a", count: 63), String(repeating: "A", count: 64),
            String(repeating: "a", count: 64) + "\n",
        ] {
            XCTAssertThrowsError(try AccountCredentialVault(scope: scope))
        }
        let vault = try AccountCredentialVault(scope: String(repeating: "a", count: 64))
        for data in [Data(), Data(repeating: 1, count: 2049)] {
            XCTAssertThrowsError(try vault.write(data)) { error in
                guard case AccountVaultFailure.invalidRecord = error else { return XCTFail("Wrong storage failure") }
            }
        }
    }
    func testRealKeychainReplacementIsolationAndRemoval() throws {
        guard ProcessInfo.processInfo.environment["SYNCWATCH_ACCOUNT_VAULT_CHECKS"] == "1" else {
            throw XCTSkip("Requires explicit owned native keychain qualification")
        }
        func scope() -> String {
            (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
        }
        let vault = try AccountCredentialVault(scope: scope())
        let independent = try AccountCredentialVault(scope: scope())
        defer {
            try? vault.remove()
            try? independent.remove()
        }
        XCTAssertNil(try vault.read())
        let first = Data([0, 1, 2, 255])
        let second = Data(repeating: 42, count: 2048)
        try vault.write(first)
        XCTAssertEqual(try vault.read(), first)
        XCTAssertNil(try independent.read())
        try vault.write(second)
        XCTAssertEqual(try vault.read(), second)
        try vault.remove()
        XCTAssertNil(try vault.read())
        try vault.remove()
    }
}
