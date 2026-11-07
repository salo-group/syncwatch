import CoreFoundation
import Foundation
import SyncWatchDomain

public protocol HelperTransport: Sendable {
    func send(_ frame: Data) async throws
    func read() async throws -> Data
    func close() async
}
extension HelperProcess: HelperTransport {}

public struct SharedMedia: Sendable {
    public let handle: String
    public let name: String
    public let durationMs: Int64
    public let fileSizeBytes: UInt64
    public init(handle: String, name: String, durationMs: Int64, fileSizeBytes: UInt64) {
        self.handle = handle
        self.name = name
        self.durationMs = durationMs
        self.fileSizeBytes = fileSizeBytes
    }
}
public struct SyncplayEndpoint: Sendable {
    public let host: String
    public let port: Int
    public let room: String
    public let username: String
    public init(host: String, port: Int, room: String, username: String) {
        self.host = host
        self.port = port
        self.room = room
        self.username = username
    }
}
public struct SyncplayParticipant: Sendable, Equatable {
    public let username: String
    public let ready: Bool
}
public struct HelperSessionState: Sendable, Equatable {
    public enum Connection: Sendable { case starting, connecting, reconnecting, connected, failed, stopped }
    public var connection: Connection = .starting
    public var ready = false
    /// Initial readiness is retained for this transport even if Syncplay later clears its user flag.
    public var playbackReady = false
    public var acceptedUsername: String?
    public var identityRejected = false
    public var participants: [SyncplayParticipant] = []
}
public enum HelperSessionFailure: String, Error { case malformed, unavailable, queueFull, timeout }

/// One owner for a borrowed native player and an owned helper transport. Callers
/// admit matching local media before construction; Syncplay alone decides timing.
/// UI intents enter synchronously on the owner, before asynchronous native work.
@MainActor public final class HelperSession {
    public private(set) var state = HelperSessionState()
    public let wireScope: PlaybackScope
    private let transport: any HelperTransport
    private let player: any PlaybackPort
    private let nativeScope: PlaybackScope
    private let endpoint: SyncplayEndpoint
    private let media: SharedMedia
    private let revision: String
    private let requireDeclaredIdentity: Bool
    private let admissionProvider: (@MainActor () async throws -> SyncplayAdmission)?
    private var admissionNonces: [String] = []
    private var pendingAdmissionNonce: String?
    private let changed: @MainActor (HelperSessionState) -> Void
    private var fence: PlaybackActionFence
    private let commands: HelperCommands
    private enum Intent {
        case pause(Bool)
        case toggle
        case seek(Int64)
        case playFrom(Int64)
    }
    private var users: [(UInt64, Intent)] = []
    private var inbox: [Data] = []
    private var corrections: [PlayerCorrection] = []
    private var completed: [(PlayerCorrection, Data)] = []
    private var replies: [(UInt64, HelperReplyKind, HelperReply)] = []
    private var commandSequence: UInt64 = 0
    private var correctionHigh: UInt64 = 0
    private var eventSequence: UInt64 = 0
    private var observationHigh: UInt64 = 0
    private var heartbeat = true
    private var observed = false
    private var readyRequested = false
    private var requestingReady = false
    private var readinessObserved = false
    private var recoveryIdentity = false
    private var recoveryConnected = false
    private var recoveryDeadline: ContinuousClock.Instant?
    private var started = false
    private var closed = false
    private var deadline: ContinuousClock.Instant?
    private var wake: CheckedContinuation<Void, Never>?
    private var reader: Task<Void, Never>?
    private var timer: Task<Void, Never>?
    private var worker: Task<Void, Never>?
    private var applying: Task<PlaybackObservation, Error>?
    private var termination: Task<Void, Never>?

    public init(
        transport: any HelperTransport, player: any PlaybackPort, nativeScope: PlaybackScope,
        endpoint: SyncplayEndpoint, media: SharedMedia, revision: String, requireDeclaredIdentity: Bool = false,
        admissionProvider: (@MainActor () async throws -> SyncplayAdmission)? = nil,
        changed: @escaping @MainActor (HelperSessionState) -> Void = { _ in }
    ) throws {
        let scope = try PlaybackScope.fresh().replacingMedia()
        let encoder = HelperCommands(wireScope: scope)
        _ = try encoder.connect(
            1, host: endpoint.host, port: endpoint.port, room: endpoint.room, username: endpoint.username,
            accountAdmission: admissionProvider != nil)
        _ = try encoder.loaded(
            1, handle: media.handle, name: media.name, durationMs: media.durationMs, fileSizeBytes: media.fileSizeBytes)
        guard revision.range(of: "\\A[0-9a-f]{40}\\z", options: .regularExpression) != nil, media.durationMs > 0 else {
            throw HelperSessionFailure.malformed
        }
        self.transport = transport
        self.player = player
        self.nativeScope = nativeScope
        self.endpoint = endpoint
        self.media = media
        self.revision = revision
        self.requireDeclaredIdentity = requireDeclaredIdentity
        self.admissionProvider = admissionProvider
        self.changed = changed
        wireScope = scope
        commands = encoder
        fence = PlaybackActionFence(scope: nativeScope)
    }

    public func start() throws {
        guard !started, !closed else { throw HelperSessionFailure.unavailable }
        started = true
        reader = Task { [self] in
            do {
                while !closed {
                    let frame = try await transport.read()
                    guard !closed else { return }
                    guard inbox.count < 64 else { throw HelperSessionFailure.queueFull }
                    inbox.append(frame)
                    signal()
                }
            } catch { if !closed { stop(failed: true) } }
        }
        timer = Task { [self] in
            while !closed {
                if deadline.map({ ContinuousClock.now >= $0 }) == true
                    || recoveryDeadline.map({ ContinuousClock.now >= $0 }) == true
                {
                    stop(failed: true)
                    return
                }
                heartbeat = true
                signal()
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            }
        }
        worker = Task { [self] in
            do { try await run() } catch { if !closed { stop(failed: true) } }
        }
    }
    public func pause(_ value: Bool) throws { try enqueue(.pause(value)) }
    public func togglePause() throws { try enqueue(.toggle) }
    public func seek(_ positionMs: Int64) throws {
        guard (0...media.durationMs).contains(positionMs) else { throw HelperSessionFailure.malformed }
        try enqueue(.seek(positionMs))
    }
    public func play(from positionMs: Int64) throws {
        guard (0...media.durationMs).contains(positionMs) else { throw HelperSessionFailure.malformed }
        try enqueue(.playFrom(positionMs))
    }
    private func enqueue(_ intent: Intent) throws {
        guard !closed, state.connection == .connected else { throw HelperSessionFailure.unavailable }
        guard users.count < 64 else { throw HelperSessionFailure.queueFull }
        users.append((try fence.beginUserAction(nativeScope), intent))
        applying?.cancel()
        signal()
    }
    public func close() async {
        if !closed { stop(failed: false) }
        await termination?.value
    }
    private func stop(failed: Bool) {
        guard !closed else { return }
        closed = true
        fence.invalidate()
        users.removeAll()
        inbox.removeAll()
        corrections.removeAll()
        state.connection = failed ? .failed : .stopped
        state.ready = false
        state.playbackReady = false
        changed(state)
        applying?.cancel()
        worker?.cancel()
        timer?.cancel()
        signal()
        let transport = transport
        let player = player
        let scope = nativeScope
        let reader = reader
        let worker = worker
        // Independent cleanup must not inherit the cancelled reader/user task.
        termination = Task.detached {
            try? await player.setPaused(true, scope: scope)
            await transport.close()
            reader?.cancel()
            await worker?.value
            await reader?.value
        }
    }
    private func signal() {
        let continuation = wake
        wake = nil
        continuation?.resume()
    }
    private func wait() async throws {
        try checkOpen()
        if inbox.isEmpty && users.isEmpty && !heartbeat { await withCheckedContinuation { wake = $0 } }
        try checkOpen()
    }
    private func checkOpen() throws {
        try Task.checkCancellation()
        if closed { throw HelperSessionFailure.unavailable }
    }
    private func nextID() throws -> UInt64 {
        guard commandSequence < PlaybackScope.maximumGeneration else { throw HelperSessionFailure.malformed }
        commandSequence += 1
        return commandSequence
    }
    private func send(_ data: Data) async throws {
        try checkOpen()
        deadline = .now.advanced(by: .seconds(5))
        try await transport.send(data)
        deadline = nil
        try checkOpen()
    }
    private func request(_ expected: HelperReplyKind, _ build: (UInt64) throws -> Data) async throws {
        let id = try nextID()
        try await send(build(id))
        let expires = ContinuousClock.now.advanced(by: .seconds(5))
        defer { deadline = nil }
        while true {
            deadline = expires
            try checkOpen()
            if !inbox.isEmpty {
                let data = inbox.removeFirst()
                let value = try HelperJSON.object(data)
                if value["replyTo"] as? String == "cmd-\(id)" {
                    let reply = try HelperReply.decode(
                        data, wireScope: wireScope, commandID: id, expected: expected, expectedRevision: revision,
                        requireAdmission: admissionProvider != nil)
                    if case .rejected = reply { throw HelperSessionFailure.unavailable }
                    replies.append((id, expected, reply))
                    if replies.count > 128 { replies.removeFirst() }
                    return
                }
                try await receive(data, value)
            } else {
                // Timer wakes this wait even when no protocol frame arrives.
                await withCheckedContinuation { wake = $0 }
            }
        }
    }
    private func run() async throws {
        deadline = .now.advanced(by: .seconds(2))
        let initial = try await player.observe(nativeScope)
        try validate(initial)
        deadline = .now.advanced(by: .seconds(2))
        try await player.setPaused(true, scope: nativeScope)
        while !(try await player.observe(nativeScope)).paused { try await Task.sleep(for: .milliseconds(20)) }
        deadline = nil
        try await request(.ready) { try commands.hello($0) }
        try await request(.applied) {
            try commands.loaded(
                $0, handle: media.handle, name: media.name, durationMs: media.durationMs,
                fileSizeBytes: media.fileSizeBytes)
        }
        state.connection = .connecting
        changed(state)
        try await request(.applied) {
            try commands.connect(
                $0, host: endpoint.host, port: endpoint.port, room: endpoint.room, username: endpoint.username,
                accountAdmission: admissionProvider != nil)
        }
        while !closed {
            try checkOpen()
            while !inbox.isEmpty {
                let data = inbox.removeFirst()
                try await receive(data, HelperJSON.object(data))
            }
            if let nonce = pendingAdmissionNonce, let admissionProvider {
                pendingAdmissionNonce = nil
                // Credential preparation shares the authenticated transport's ten-second admission window.
                deadline = .now.advanced(by: .seconds(10))
                let credential = try await admissionProvider()
                try checkOpen()
                deadline = nil
                try await request(.applied) { try commands.admit($0, nonce: nonce, authority: credential) }
                continue
            }
            try await cancelSuperseded()
            if !users.isEmpty {
                let intent = users.removeFirst()
                try await user(intent.0, intent.1)
                continue
            }
            if let first = corrections.first {
                if try fence.correction(nativeScope, afterUserAction: first.afterUserAction) == .apply {
                    try await correct(first)
                    continue
                }
            }
            if heartbeat {
                heartbeat = false
                deadline = .now.advanced(by: .seconds(2))
                let value = try await player.observe(nativeScope)
                deadline = nil
                try validate(value)
                // VLC sequences identify events, not reads. A fresh paused sample
                // must reach Syncplay after login even when its event sequence is unchanged.
                if value.sequence >= observationHigh {
                    try await send(commands.observation(nextID(), value: value, nativeScope: nativeScope))
                    observationHigh = value.sequence
                    observed = true
                }
            }
            if (state.connection == .connected || recoveryConnected) && observed && !readyRequested {
                requestingReady = true
                try await request(.applied) { try commands.ready($0, value: true) }
                readyRequested = true
                requestingReady = false
                finishReadiness()
                continue
            }
            try await wait()
        }
    }
    private func reconnect() async throws {
        // An unpublished intent or an old timing correction cannot safely be
        // replayed across transports. Keep the fence and fail closed instead.
        guard users.isEmpty, fence.issuedSequence == fence.publishedSequence,
            corrections.allSatisfy({ $0.operation == .pause(true) })
        else { throw HelperSessionFailure.unavailable }
        if recoveryDeadline == nil { recoveryDeadline = .now.advanced(by: .seconds(25)) }
        recoveryIdentity = false
        recoveryConnected = false
        observed = false
        readyRequested = false
        requestingReady = false
        readinessObserved = false
        state.connection = .reconnecting
        state.ready = false
        state.playbackReady = false
        state.participants = []
        changed(state)
        deadline = .now.advanced(by: .seconds(2))
        defer { deadline = nil }
        try await player.setPaused(true, scope: nativeScope)
        while true {
            let value = try await player.observe(nativeScope)
            try validate(value)
            if value.paused { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        heartbeat = true
    }
    private func finishReadiness() {
        guard observed, readyRequested, readinessObserved || state.ready else { return }
        if recoveryDeadline != nil {
            guard recoveryConnected else { return }
            recoveryDeadline = nil
            recoveryConnected = false
            state.connection = .connected
        }
        guard state.connection == .connected else { return }
        state.playbackReady = true
        changed(state)
    }
    private func user(_ sequence: UInt64, _ intent: Intent) async throws {
        var targetPause: Bool?
        var targetPosition: Int64?
        switch intent {
        case .pause(let value): targetPause = value
        case .toggle: targetPause = !(try await player.observe(nativeScope)).paused
        case .seek(let position): targetPosition = position
        case .playFrom(let position):
            targetPosition = position
            targetPause = false
        }
        deadline = .now.advanced(by: .seconds(2))
        if let targetPosition { try await player.seek(to: targetPosition, scope: nativeScope) }
        if let targetPause { try await player.setPaused(targetPause, scope: nativeScope) }
        deadline = .now.advanced(by: .seconds(2))
        defer { deadline = nil }
        while true {
            try checkOpen()
            let actual = try await player.observe(nativeScope)
            try validate(actual)
            if targetPause.map({ $0 == actual.paused }) ?? true,
                targetPosition.map({ abs($0 - actual.positionMs) <= 250 }) ?? true
            {
                guard try fence.canPublish(sequence, owner: nativeScope) else { return }
                try await send(
                    commands.userObservation(nextID(), sequence: sequence, value: actual, nativeScope: nativeScope))
                try fence.didPublish(sequence, observation: actual)
                observationHigh = actual.sequence
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        }
    }
    private func correct(_ command: PlayerCorrection) async throws {
        let task = Task {
            try await command.apply(to: player, nativeScope: nativeScope, authorizedHandle: media.handle)
        }
        applying = task
        deadline = .now.advanced(by: .seconds(2))
        defer {
            applying = nil
            deadline = nil
        }
        do {
            let actual = try await task.value
            try checkOpen()
            if try fence.correction(nativeScope, afterUserAction: command.afterUserAction) == .apply {
                let data = try command.result(actual, nativeScope: nativeScope)
                try await send(data)
                observationHigh = max(observationHigh, actual.sequence)
                complete(command, data)
            }
        } catch is CancellationError {
            if fence.issuedSequence <= command.afterUserAction { throw HelperSessionFailure.unavailable }
        }
    }
    private func cancelSuperseded() async throws {
        for command in corrections where command.afterUserAction < fence.publishedSequence {
            let data = try command.cancelledReply()
            try await send(data)
            complete(command, data)
        }
    }
    private func complete(_ command: PlayerCorrection, _ result: Data) {
        corrections.removeAll { $0.id == command.id }
        completed.append((command, result))
        if completed.count > 128 { completed.removeFirst() }
    }
    private func receive(_ data: Data, _ value: [String: Any]) async throws {
        guard let kind = value["type"] as? String else { throw HelperSessionFailure.malformed }
        if value["replyTo"] != nil {
            guard let record = replies.first(where: { value["replyTo"] as? String == "cmd-\($0.0)" }),
                try HelperReply.decode(
                    data, wireScope: wireScope, commandID: record.0, expected: record.1, expectedRevision: revision,
                    requireAdmission: admissionProvider != nil)
                    == record.2
            else { throw HelperSessionFailure.malformed }
            return
        }
        if kind.hasPrefix("player.") {
            let command = try PlayerCorrection.decode(data, wireScope: wireScope)
            if let record = completed.first(where: { $0.0.id == command.id }) {
                guard record.0 == command else { throw HelperSessionFailure.malformed }
                try await send(record.1)
                return
            }
            if let pending = corrections.first(where: { $0.id == command.id }) {
                guard pending == command else { throw HelperSessionFailure.malformed }
                return
            }
            guard let number = UInt64(command.id.dropFirst(4)), number > correctionHigh,
                command.afterUserAction <= fence.issuedSequence, corrections.count < 64
            else { throw HelperSessionFailure.malformed }
            correctionHigh = number
            corrections.append(command)
            return
        }
        try HelperJSON.keys(value, required: ["v", "id", "type", "clientRunId", "mediaGeneration", "payload"])
        guard try HelperJSON.integer(value["v"], maximum: 1) == 1, let run = value["clientRunId"] as? String,
            let id = value["id"] as? String, id.hasPrefix("event-"),
            let ordinal = UInt64(id.dropFirst(6)), id == "event-\(ordinal)", ordinal == eventSequence + 1,
            let payload = value["payload"] as? [String: Any]
        else { throw HelperSessionFailure.malformed }
        try wireScope.check(
            clientRunID: run,
            mediaGeneration: HelperJSON.integer(value["mediaGeneration"], maximum: PlaybackScope.maximumGeneration))
        eventSequence = ordinal
        switch kind {
        case "sync.admissionRequired":
            try HelperJSON.keys(payload, required: ["nonce"])
            let nonce = try text(payload["nonce"], maximum: 32)
            guard admissionProvider != nil, pendingAdmissionNonce == nil,
                [.connecting, .reconnecting].contains(state.connection),
                nonce.range(of: "\\A[0-9a-f]{32}\\z", options: .regularExpression) != nil,
                !admissionNonces.contains(nonce)
            else { throw HelperSessionFailure.malformed }
            if admissionNonces.count == 128 { admissionNonces.removeFirst() }
            admissionNonces.append(nonce)
            pendingAdmissionNonce = nonce
        case "sync.identity":
            try HelperJSON.keys(payload, required: ["username"])
            let username = try text(payload["username"], maximum: 64)
            guard !requireDeclaredIdentity || username == endpoint.username else {
                state.identityRejected = true
                throw HelperSessionFailure.unavailable
            }
            guard state.acceptedUsername == nil || state.acceptedUsername == username else {
                state.identityRejected = true
                throw HelperSessionFailure.unavailable
            }
            state.acceptedUsername = username
            if recoveryDeadline != nil { recoveryIdentity = true }
        case "sync.connection":
            try HelperJSON.keys(payload, required: ["state"], optional: ["error"])
            if let error = payload["error"] {
                // Validate the common error shape through the existing strict reply decoder.
                var wrapped = try JSONSerialization.data(withJSONObject: [
                    "v": 1, "replyTo": "cmd-1", "type": "bridge.error", "clientRunId": wireScope.clientRunID,
                    "mediaGeneration": wireScope.mediaGeneration, "payload": error,
                ])
                wrapped.append(10)
                _ = try HelperReply.decode(wrapped, wireScope: wireScope, commandID: 1, expected: .applied)
            }
            switch payload["state"] as? String {
            case "connecting":
                if state.connection == .connected || state.connection == .reconnecting {
                    try await reconnect()
                } else {
                    state.connection = .connecting
                }
            case "connected":
                guard state.acceptedUsername != nil else { throw HelperSessionFailure.malformed }
                if recoveryDeadline != nil {
                    guard recoveryIdentity else { throw HelperSessionFailure.malformed }
                    recoveryConnected = true
                    observed = false
                    heartbeat = true
                } else {
                    state.connection = .connected
                }
            case "disconnected": throw HelperSessionFailure.unavailable
            default: throw HelperSessionFailure.malformed
            }
        case "sync.readiness":
            try HelperJSON.keys(payload, required: ["ready"])
            state.ready = try boolean(payload["ready"])
            if state.ready, observed, requestingReady || readyRequested { readinessObserved = true }
            if recoveryDeadline != nil, state.ready {
                guard recoveryConnected, observed, requestingReady || readyRequested else {
                    throw HelperSessionFailure.malformed
                }
            }
            finishReadiness()
        case "sync.participants":
            try HelperJSON.keys(payload, required: ["participants"])
            guard let rows = payload["participants"] as? [[String: Any]], rows.count <= 64 else {
                throw HelperSessionFailure.malformed
            }
            state.participants = try rows.map {
                try HelperJSON.keys($0, required: ["username", "ready"])
                return SyncplayParticipant(
                    username: try text($0["username"], maximum: 64), ready: try boolean($0["ready"]))
            }
        default: throw HelperSessionFailure.malformed
        }
        changed(state)
    }
    private func validate(_ value: PlaybackObservation) throws {
        try nativeScope.check(clientRunID: value.scope.clientRunID, mediaGeneration: value.scope.mediaGeneration)
        guard value.status == .ready, value.durationMs == media.durationMs,
            value.seekable || (value.ended && value.paused)
        else {
            throw HelperSessionFailure.unavailable
        }
    }
    private func boolean(_ value: Any?) throws -> Bool {
        guard let value = value as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else {
            throw HelperSessionFailure.malformed
        }
        return value.boolValue
    }
    private func text(_ value: Any?, maximum: Int) throws -> String {
        guard let value = value as? String, (1...maximum).contains(value.unicodeScalars.count),
            value.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value != 127 })
        else { throw HelperSessionFailure.malformed }
        return value
    }
}
