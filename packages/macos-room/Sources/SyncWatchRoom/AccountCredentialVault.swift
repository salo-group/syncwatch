import Foundation
import LocalAuthentication
import Security
import SyncWatchKeychainPolicy

public enum AccountVaultFailure: Error { case unavailable, invalidRecord }

/// Stores only an opaque, bounded SyncWatch device credential in the local OS keychain.
/// Authentication UI is deliberately disabled; a locked store requires an explicit memory-only session.
public struct AccountCredentialVault: Sendable {
    private let scope: String
    public init(scope: String) throws {
        guard scope.utf8.count == 64, scope.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
        else {
            throw AccountVaultFailure.invalidRecord
        }
        guard SWKeychainDisallowInteraction() == errSecSuccess else { throw AccountVaultFailure.unavailable }
        self.scope = scope
    }
    private var query: [String: Any] {
        let context = LAContext()
        context.interactionNotAllowed = true
        return [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "dev.syncwatch.device-session.v1",
            kSecAttrAccount as String: scope,
            kSecAttrSynchronizable as String: false,
            kSecUseAuthenticationContext as String: context,
        ]
    }
    public func read() throws -> Data? {
        var query = query
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw AccountVaultFailure.unavailable }
        guard let data = result as? Data, (1...2048).contains(data.count) else {
            throw AccountVaultFailure.invalidRecord
        }
        return data
    }
    public func write(_ data: Data) throws {
        guard (1...2048).contains(data.count) else { throw AccountVaultFailure.invalidRecord }
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw AccountVaultFailure.unavailable }
    }
    public func remove() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw AccountVaultFailure.unavailable }
    }
}
