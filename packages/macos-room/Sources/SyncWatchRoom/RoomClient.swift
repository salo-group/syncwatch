import Foundation

final class RoomSessionDelegate: NSObject, URLSessionWebSocketDelegate {
    let termination = RoomSocketTermination()

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard task is URLSessionWebSocketTask else { return }
        termination.finish()
    }
    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
    ) {
        termination.finish()
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

/// One room attempt owns all network work; close cancels and joins it before credentials are released.
public actor RoomClient {
    public let address: RoomAddress
    let authority: RoomAccountAuthority?
    private var eventOpening: Task<RoomEventStream, Error>?
    private let session: URLSession
    private let eventSession: URLSession
    private let delegate: RoomSessionDelegate
    private var requests: [UUID: Task<Data, Error>] = [:]
    private var closed = false
    private var eventStream: RoomEventStream?
    public init(address: RoomAddress) { self.init(address: address, authority: nil) }
    public init(authority: RoomAccountAuthority) { self.init(address: authority.address, authority: authority) }
    private init(address: RoomAddress, authority: RoomAccountAuthority?) {
        self.address = address
        self.authority = authority
        let configuration = Self.configuration()
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 20
        let delegate = RoomSessionDelegate()
        self.delegate = delegate
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        // A live event stream must not inherit the short whole-response HTTP deadline.
        // Bootstrap is bounded by the controller; the SDK retains its normal stream limits.
        eventSession = URLSession(configuration: Self.configuration(), delegate: delegate, delegateQueue: nil)
    }
    private static func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpMaximumConnectionsPerHost = 2
        return configuration
    }

    public func create(identity: RoomIdentity, connection: RoomConnection, creationKey: RoomSecret) async throws
        -> RoomAdmission
    {
        guard authority == nil else { throw RoomProtocolFailure.malformed }
        let body = try RoomWire.encode([
            "v": 1, "clientRunId": identity.clientRunID, "syncplayUsername": identity.syncplayUsername,
            "syncplayHost": connection.host, "syncplayPort": connection.port, "syncplayRoom": connection.room,
        ])
        return try RoomAdmission.decode(
            await request("POST", "v1/rooms", credential: creationKey, body: body), identity: identity, owner: true)
    }
    public func join(roomID: String, invitation: RoomSecret, identity: RoomIdentity) async throws -> RoomAdmission {
        guard authority == nil else { throw RoomProtocolFailure.malformed }
        try RoomWire.id(roomID)
        let body = try RoomWire.encode([
            "v": 1, "invitation": invitation.value, "clientRunId": identity.clientRunID,
            "syncplayUsername": identity.syncplayUsername,
        ])
        return try RoomAdmission.decode(
            await request("POST", "v1/rooms/\(roomID)/join", body: body), identity: identity, owner: false)
    }
    public func snapshot(_ admission: RoomAdmission) async throws -> RoomBootstrap {
        try RoomBootstrap.decode(
            await request("GET", "\(version)/rooms/\(admission.roomID)/snapshot", admission: admission))
    }
    public func catalog(_ admission: RoomAdmission, bootstrap: RoomBootstrap, cursor: String) async throws
        -> RoomCatalogPage
    {
        guard bootstrap.snapshot.epoch == admission.bootstrap.snapshot.epoch else {
            throw RoomProtocolFailure.malformed
        }
        _ = try RoomCatalogPosition(cursor, bootstrap: bootstrap)
        return try RoomCatalogPage.decode(
            await request("GET", "\(version)/rooms/\(admission.roomID)/catalog", admission: admission, cursor: cursor),
            bootstrap: bootstrap, cursor: cursor)
    }
    /// The caller serializes bootstrap creation with this walk and bounds its overall lifetime.
    public func catalogView(_ admission: RoomAdmission, bootstrap: RoomBootstrap) async throws -> RoomCatalogView {
        guard bootstrap.snapshot.epoch == admission.bootstrap.snapshot.epoch else {
            throw RoomProtocolFailure.malformed
        }
        return try await RoomCatalogView.read(bootstrap: bootstrap) { cursor in
            try await self.request(
                "GET", "\(version)/rooms/\(admission.roomID)/catalog", admission: admission, cursor: cursor)
        }
    }
    public func selection(_ admission: RoomAdmission, bootstrap: RoomBootstrap) async throws -> RoomSelectionDefinition
    {
        guard bootstrap.snapshot.epoch == admission.bootstrap.snapshot.epoch else {
            throw RoomProtocolFailure.malformed
        }
        return try RoomSelectionDefinition.decode(
            await request(
                "GET", "\(version)/rooms/\(admission.roomID)/selection", admission: admission,
                generation: bootstrap.snapshot.watchGeneration), bootstrap: bootstrap)
    }
    public func leave(_ admission: RoomAdmission) async throws {
        let bytes = try await request("POST", "\(version)/rooms/\(admission.roomID)/leave", admission: admission)
        if authority != nil { try RoomAccessWire.status(bytes, "left"); return }
        let value = try RoomWire.object(bytes)
        try RoomWire.keys(value, ["v", "status"])
        guard try RoomWire.integer(value["v"], maximum: 2) == 1, value["status"] as? String == "left" else {
            throw RoomProtocolFailure.malformed
        }
    }
    public func execute(_ admission: RoomAdmission, command: RoomCommand) async throws -> RoomCommandResult {
        try command.verify(admission)
        return try RoomCommandResult.decode(
            await request(
                "POST", "\(version)/rooms/\(admission.roomID)/commands", admission: admission, body: command.encodedBytes
            ),
            command: command)
    }
    /// Attach immediately after admission, before hashing media or performing other long work.
    public func openEvents(_ admission: RoomAdmission) async throws -> RoomEventStream {
        try Task.checkCancellation()
        guard !closed, eventStream == nil else { throw RoomProtocolFailure.closed }
        guard eventOpening == nil else { throw RoomProtocolFailure.closed }
        try validate(admission)
        let pending = Task { try await self.connectEvents(admission) }
        eventOpening = pending
        defer { eventOpening = nil }
        return try await withTaskCancellationHandler(operation: { try await pending.value }, onCancel: { pending.cancel() })
    }
    private func connectEvents(_ admission: RoomAdmission) async throws -> RoomEventStream {
        var parts = URLComponents(url: address.endpoint("\(version)/rooms/\(admission.roomID)/events"), resolvingAgainstBaseURL: false)!
        parts.scheme = address.origin.scheme == "https" ? "wss" : "ws"
        var request = URLRequest(url: parts.url!)
        if let authority {
            let credential = try await authority.credential()
            request.setValue("Bearer \(credential.access)", forHTTPHeaderField: "Authorization")
            try memberHeaders(admission, request: &request)
        } else if let token = admission.token {
            request.setValue("Bearer \(token.value)", forHTTPHeaderField: "Authorization")
        }
        try Task.checkCancellation()
        guard !closed else { throw RoomProtocolFailure.closed }
        let stream = RoomEventStream(socket: eventSession.webSocketTask(with: request))
        eventStream = stream
        delegate.termination.bind(stream)
        await stream.start()
        guard !closed, !Task.isCancelled else {
            await stream.close()
            throw RoomProtocolFailure.closed
        }
        return stream
    }
    public func close() async {
        closed = true
        let opening = eventOpening
        opening?.cancel()
        let pending = Array(requests.values)
        for task in pending { task.cancel() }
        session.invalidateAndCancel()
        eventSession.invalidateAndCancel()
        if let eventStream { await eventStream.close() }
        for task in pending { _ = await task.result }
        _ = await opening?.result
    }
    var version: String { authority == nil ? "v1" : "v2" }
    private func validate(_ admission: RoomAdmission) throws {
        try RoomWire.id(admission.roomID)
        try RoomWire.id(admission.memberID)
        guard let _ = admission.bootstrap.snapshot.members.first(where: { $0.memberID == admission.memberID }),
            authority.map({ admission.accountScope == $0.scope && admission.token == nil })
                ?? (admission.accountScope == nil && admission.token != nil) else { throw RoomProtocolFailure.malformed }
    }
    private func memberHeaders(_ admission: RoomAdmission, request: inout URLRequest) throws {
        try validate(admission)
        let identity = admission.bootstrap.snapshot.members.first(where: { $0.memberID == admission.memberID })!.identity
        request.setValue(admission.memberID, forHTTPHeaderField: "X-SyncWatch-Member")
        request.setValue(identity.clientRunID, forHTTPHeaderField: "X-SyncWatch-Run")
    }
    func request(
        _ method: String, _ path: String, credential: RoomSecret? = nil, admission: RoomAdmission? = nil, body: Data? = nil, cursor: String? = nil,
        generation: UInt64? = nil, runID: String? = nil
    )
        async throws -> Data
    {
        try Task.checkCancellation()
        guard !closed else { throw RoomProtocolFailure.closed }
        if let admission { try validate(admission) }
        var parts = URLComponents(url: address.endpoint(path), resolvingAgainstBaseURL: false)!
        if let cursor { parts.queryItems = [URLQueryItem(name: "cursor", value: cursor)] }
        if let generation { parts.queryItems = [URLQueryItem(name: "watchGeneration", value: String(generation))] }
        var request = URLRequest(url: parts.url!)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let admission, authority != nil { try memberHeaders(admission, request: &request) }
        if let runID { try RoomWire.id(runID); request.setValue(runID, forHTTPHeaderField: "X-SyncWatch-Run") }
        if let credential = credential ?? admission?.token { request.setValue("Bearer \(credential.value)", forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let id = UUID()
        let initial = request
        let task = Task {
            var request = initial
            let previous = try await self.authority?.credential()
            if let previous { request.setValue("Bearer \(previous.access)", forHTTPHeaderField: "Authorization") }
            var response = try await Self.transfer(self.session, request: request)
            if response.status == 401, let previous, let authority = self.authority {
                let current = try await authority.credential()
                if current.access != previous.access {
                    request.setValue("Bearer \(current.access)", forHTTPHeaderField: "Authorization")
                    response = try await Self.transfer(self.session, request: request)
                }
            }
            return try Self.decode(response, account: self.authority != nil)
        }
        requests[id] = task
        defer { requests.removeValue(forKey: id) }
        let data = try await withTaskCancellationHandler(
            operation: { try await task.value }, onCancel: { task.cancel() })
        try Task.checkCancellation()
        guard !closed else { throw RoomProtocolFailure.closed }
        return data
    }
    private struct Response: Sendable { let status: Int; let bytes: Data }
    private static func transfer(_ session: URLSession, request: URLRequest) async throws -> Response {
        let (stream, response) = try await session.bytes(for: request)
        defer { stream.task.cancel() }
        guard let response = response as? HTTPURLResponse, response.url == request.url,
            response.mimeType?.lowercased() == "application/json"
        else { throw RoomProtocolFailure.transport }
        guard response.expectedContentLength <= RoomWire.maximumBytes else { throw RoomProtocolFailure.frameTooLarge }
        var data = Data()
        for try await byte in stream {
            try Task.checkCancellation()
            guard data.count < RoomWire.maximumBytes else { throw RoomProtocolFailure.frameTooLarge }
            data.append(byte)
        }
        return Response(status: response.statusCode, bytes: data)
    }
    private static func decode(_ response: Response, account: Bool) throws -> Data {
        let data = response.bytes
        guard (200...299).contains(response.status) else {
            // Never publish server text, headers or echoed request credentials as an error.
            let value = try RoomWire.object(data)
            try RoomWire.keys(value, account ? ["v", "error"] : ["error"])
            if account, try RoomWire.integer(value["v"], maximum: 2) != 2 { throw RoomProtocolFailure.malformed }
            let error = try RoomWire.dictionary(value["error"])
            try RoomWire.keys(error, ["code"])
            let code = try RoomWire.string(error["code"])
            let allowed = [
                "unauthorized", "forbidden", "notFound", "conflict", "expired", "queueFull", "tlsRequired", "malformed",
                "frameTooLarge", "internal", "snapshotRequired", "duplicate", "staleGeneration",
                "accountSignInRequired", "admissionRequired", "unavailable",
            ]
            throw RoomProtocolFailure.rejected(allowed.contains(code) ? code : "transport")
        }
        return try account ? RoomAccessWire.result(data) : data
    }
}
