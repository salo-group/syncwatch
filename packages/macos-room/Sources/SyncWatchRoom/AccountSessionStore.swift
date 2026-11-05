import CryptoKit
import Darwin
import Foundation

/// A versioned refresh checkpoint. Access tokens, names and emails never enter this record.
public struct AccountSavedSession: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let service: String
    let accountID: String
    let refreshToken: String
    let requestID: String
    public var description: String { "[saved account redacted]" }
    public var debugDescription: String { description }
    init(service: String, accountID: String, refreshToken: String, requestID: String) throws {
        guard service.utf8.count <= 256 else { throw RoomProtocolFailure.malformed }
        _ = try RoomAddress(service)
        for value in [accountID, refreshToken, requestID] {
            guard value.utf8.count == 43,
                value.utf8.allSatisfy({
                    (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 95 || $0 == 45
                })
            else { throw RoomProtocolFailure.malformed }
        }
        self.service = service
        self.accountID = accountID
        self.refreshToken = refreshToken
        self.requestID = requestID
    }
    public static func decode(_ bytes: Data) throws -> Self {
        guard (1...2048).contains(bytes.count) else { throw RoomProtocolFailure.malformed }
        let object = try RoomWire.object(bytes)
        try RoomWire.keys(object, ["v", "service", "accountId", "refreshToken", "requestId"])
        guard try RoomWire.integer(object["v"], maximum: 1) == 1 else { throw RoomProtocolFailure.malformed }
        return try Self(
            service: RoomWire.string(object["service"]), accountID: RoomWire.string(object["accountId"]),
            refreshToken: RoomWire.string(object["refreshToken"]), requestID: RoomWire.string(object["requestId"]))
    }
    func encode() throws -> Data {
        let bytes = try JSONSerialization.data(withJSONObject: [
            "v": 1, "service": service, "accountId": accountID,
            "refreshToken": refreshToken, "requestId": requestID,
        ])
        guard bytes.count <= 2048 else { throw RoomProtocolFailure.malformed }
        return bytes
    }
}

/// One profile owns its saved sign-in. The lease file contains no credential or account data.
public actor AccountSessionStore {
    private let directory: URL
    private var descriptor: Int32 = -1
    private var storage: AccountCredentialVault?
    private var closed = false
    public init(directory: URL) { self.directory = directory.standardizedFileURL }
    private func vault() throws -> AccountCredentialVault {
        guard !closed else { throw AccountVaultFailure.unavailable }
        if let storage { return storage }
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        let file = directory.appendingPathComponent("account-session.lock")
        let fd = Darwin.open(file.path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw AccountVaultFailure.unavailable }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(fd)
            throw AccountVaultFailure.unavailable
        }
        do {
            let scope = SHA256.hash(data: Data(directory.resolvingSymlinksInPath().path.utf8)).map {
                String(format: "%02x", $0)
            }.joined()
            let storage = try AccountCredentialVault(scope: scope)
            self.storage = storage
            descriptor = fd
            return storage
        } catch {
            Darwin.close(fd)
            throw error
        }
    }
    public func read() throws -> AccountSavedSession? { try vault().read().map(AccountSavedSession.decode) }
    public func save(_ record: AccountSavedSession) throws { try vault().write(record.encode()) }
    public func forget() throws { try vault().remove() }
    public func close() {
        closed = true
        storage = nil
        if descriptor >= 0 {
            Darwin.close(descriptor)
            descriptor = -1
        }
    }
    deinit { if descriptor >= 0 { Darwin.close(descriptor) } }
}
