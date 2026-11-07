import CryptoKit
import Foundation
import Security

public struct AccountProfile: Sendable, Equatable {
    public let id: String
    public let name: String
    public let email: String
}

/// Kept in memory; diagnostic representations never include either token.
public struct AccountDeviceSession: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let account: AccountProfile
    fileprivate let access: String
    fileprivate let refresh: String
    fileprivate let issuer: URL?
    fileprivate let credentialOwner: UUID?
    init(account: AccountProfile, access: String, refresh: String, issuer: URL? = nil, credentialOwner: UUID? = nil) {
        self.account = account
        self.access = access
        self.refresh = refresh
        self.issuer = issuer
        self.credentialOwner = credentialOwner
    }
    func roomCredential() throws -> AccountRoomCredential {
        guard let issuer, let credentialOwner else { throw RoomProtocolFailure.malformed }
        return AccountRoomCredential(origin: issuer, owner: credentialOwner, accountID: account.id, access: access)
    }
    public var description: String { "[account session redacted]" }
    public var debugDescription: String { description }
}

public struct AccountSignIn: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public let browserURI: URL
    fileprivate let id: String
    fileprivate let proof: String
    fileprivate let confirmation: String
    public var description: String { "[account sign-in redacted]" }
    public var debugDescription: String { description }
}

public struct AccountSignInStatus: Sendable {
    public let state: String
    public let account: AccountProfile?
}

/// Account API is independent of room admission. No Google token reaches this client.
public actor AccountClient {
    private nonisolated let address: RoomAddress
    private nonisolated let credentialOwner = UUID()
    private let session: URLSession
    private let delegate = RoomSessionDelegate()
    public init(address: RoomAddress) {
        self.address = address
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }
    public nonisolated func roomAuthority(_ device: AccountDeviceSession) throws -> RoomAccountAuthority {
        try RoomAccountAuthority(address: address, device: device, owner: credentialOwner)
    }
    public func start() async throws -> AccountSignIn {
        let proof = try Self.secret()
        let challenge = Self.challenge(proof)
        let result = try await request("sign-ins", body: ["challenge": challenge])
        let id = try Self.token(result["transactionId"])
        let uri = address.endpoint("auth/google/start").absoluteString + "?id=" + id
        guard result["browserUri"] as? String == uri, let url = URL(string: uri),
            try RoomWire.integer(result["expiresIn"], maximum: 300) == 300,
            try RoomWire.integer(result["pollAfterMs"], maximum: 2000) == 2000
        else { throw RoomProtocolFailure.malformed }
        return AccountSignIn(browserURI: url, id: id, proof: proof, confirmation: try Self.secret())
    }
    public func poll(_ signIn: AccountSignIn) async throws -> AccountSignInStatus {
        let result = try await request("sign-ins/" + signIn.id + "/poll", body: ["proof": signIn.proof])
        guard let state = result["status"] as? String,
            [
                "pending", "authorizing", "browserConfirmationRequired", "confirmAccount", "complete", "cancelled",
                "failed",
            ].contains(state)
        else { throw RoomProtocolFailure.malformed }
        let account = state == "confirmAccount" ? try Self.profile(result["account"], proposal: true) : nil
        return AccountSignInStatus(state: state, account: account)
    }
    public func verifyBrowser(_ signIn: AccountSignIn, browserProof: String) async throws -> AccountProfile {
        let code = browserProof.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "-", with: "")
            .uppercased()
        guard code.utf8.count == 12, code.range(of: "^[0-9A-F]{12}$", options: .regularExpression) != nil else {
            throw RoomProtocolFailure.malformed
        }
        let result = try await request(
            "sign-ins/" + signIn.id + "/verify", body: ["proof": signIn.proof, "browserProof": code])
        guard result["status"] as? String == "confirmAccount" else { throw RoomProtocolFailure.malformed }
        return try Self.profile(result["account"], proposal: true)
    }
    public func confirm(_ signIn: AccountSignIn, browserProof: String) async throws -> AccountDeviceSession {
        let code = browserProof.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "-", with: "")
            .uppercased()
        guard code.utf8.count == 12, code.range(of: "^[0-9A-F]{12}$", options: .regularExpression) != nil else {
            throw RoomProtocolFailure.malformed
        }
        let result = try await request(
            "sign-ins/" + signIn.id + "/confirm",
            body: ["proof": signIn.proof, "requestId": signIn.confirmation, "browserProof": code])
        guard result["status"] as? String == "complete" else { throw RoomProtocolFailure.malformed }
        return try device(result["session"])
    }
    public func cancel(_ signIn: AccountSignIn) async throws {
        _ = try await request("sign-ins/" + signIn.id + "/cancel", body: ["proof": signIn.proof])
    }
    public func refresh(_ device: AccountDeviceSession) async throws -> AccountDeviceSession {
        try await resume(checkpoint(device))
    }
    public func checkpoint(_ device: AccountDeviceSession) throws -> AccountSavedSession {
        try AccountSavedSession(
            service: address.origin.absoluteString, accountID: device.account.id,
            refreshToken: device.refresh, requestID: Self.secret())
    }
    public func resume(_ saved: AccountSavedSession) async throws -> AccountDeviceSession {
        guard try RoomAddress(saved.service).origin == address.origin else { throw RoomProtocolFailure.malformed }
        // Retry only identical request bytes so a lost reply cannot look like refresh-token theft.
        let body = ["refreshToken": saved.refreshToken, "requestId": saved.requestID]
        let result: AccountDeviceSession
        do { result = try device(await request("refresh", body: body)["session"]) } catch is URLError {
            try Task.checkCancellation()
            result = try device(await request("refresh", body: body)["session"])
        }
        guard result.account.id == saved.accountID else {
            try? await logout(result)
            throw RoomProtocolFailure.malformed
        }
        return result
    }
    public func logout(_ device: AccountDeviceSession) async throws {
        _ = try await request("logout", bearer: device.refresh)
    }
    public func close() { session.invalidateAndCancel() }

    private func request(_ path: String, body: [String: String]? = nil, bearer: String? = nil) async throws -> [String:
        Any]
    {
        var request = URLRequest(url: address.endpoint("v2/account/" + path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let bearer { request.setValue("Bearer " + bearer, forHTTPHeaderField: "Authorization") }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            var object: [String: Any] = body
            object["v"] = 2
            request.httpBody = try JSONSerialization.data(withJSONObject: object)
        }
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse, http.url == request.url, http.mimeType == "application/json",
            http.expectedContentLength <= 16384
        else { throw RoomProtocolFailure.transport }
        var data = Data()
        for try await byte in bytes {
            guard data.count < 16384 else { throw RoomProtocolFailure.frameTooLarge }
            data.append(byte)
        }
        try Task.checkCancellation()
        let envelope = try RoomWire.object(data)
        guard try RoomWire.integer(envelope["v"], maximum: 2) == 2
        else { throw RoomProtocolFailure.malformed }
        guard (200...299).contains(http.statusCode) else {
            throw RoomProtocolFailure.rejected(http.statusCode == 401 ? "accountSignInRequired" : "accountUnavailable")
        }
        guard let result = envelope["result"] as? [String: Any] else { throw RoomProtocolFailure.malformed }
        return result
    }
    private func device(_ value: Any?) throws -> AccountDeviceSession {
        guard let object = value as? [String: Any], try RoomWire.integer(object["expiresIn"], maximum: 600) == 600
        else { throw RoomProtocolFailure.malformed }
        return try AccountDeviceSession(
            account: Self.profile(object["account"]), access: Self.token(object["accessToken"]),
            refresh: Self.token(object["refreshToken"]), issuer: address.origin, credentialOwner: credentialOwner)
    }
    static func profile(_ value: Any?, proposal: Bool = false) throws -> AccountProfile {
        guard let object = value as? [String: Any], let id = object["id"] as? String,
            proposal ? id.isEmpty : isToken(id), let name = object["name"] as? String,
            let email = object["email"] as? String,
            !name.isEmpty, name.utf16.count <= 128, !email.isEmpty, email.utf16.count <= 320,
            !(name + email).unicodeScalars.contains(where: { $0.properties.generalCategory == .control })
        else { throw RoomProtocolFailure.malformed }
        return AccountProfile(id: id, name: name, email: email)
    }
    static func token(_ value: Any?) throws -> String {
        guard let value = value as? String, isToken(value) else { throw RoomProtocolFailure.malformed }
        return value
    }
    private static func isToken(_ value: String) -> Bool {
        value.range(of: "\\A[A-Za-z0-9_-]{43}\\z", options: .regularExpression) != nil
    }
    static func challenge(_ proof: String) -> String { base64(Data(SHA256.hash(data: Data(proof.utf8)))) }
    private static func base64(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    static func secret() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw RoomProtocolFailure.transport
        }
        return base64(Data(bytes))
    }
}
