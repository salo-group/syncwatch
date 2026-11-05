import Foundation
import SyncWatchDomain
import Testing

@testable import SyncWatchBridge

private let sessionRevision = String(repeating: "a", count: 40)

/// A scripted peer, not a replacement synchronization engine. In particular the
/// write gate lets a reverse request arrive before a user publication completes.
private actor SessionPeer: HelperTransport {
    private var context: [String: Any] = [:]
    private var incoming: [Data] = []
    private var sent: [Data] = []
    private var reading: CheckedContinuation<Data, Error>?
    private var writing: CheckedContinuation<Void, Never>?
    private var event = 0
    private var ended = false
    private var holdUser = false
    private var stationaryHandshake = false
    private var samples = 0
    private var ticketMode = false
    private var advertiseAdmission = true
    private var automaticReady = true
    private var pulseBeforeAck: Bool?
    private(set) var userWriteHeld = false

    private func encode(_ value: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) + Data([10])
    }
    private func push(_ value: [String: Any]) throws {
        let data = try encode(value)
        if let current = reading {
            reading = nil
            current.resume(returning: data)
        } else {
            incoming.append(data)
        }
    }
    private func base(_ kind: String, _ payload: [String: Any]) -> [String: Any] {
        [
            "v": 1, "clientRunId": context["clientRunId"]!, "mediaGeneration": context["mediaGeneration"]!,
            "type": kind, "payload": payload,
        ]
    }
    private func emit(_ kind: String, _ payload: [String: Any]) throws {
        event += 1
        var value = base(kind, payload)
        value["id"] = "event-\(event)"
        try push(value)
    }
    func send(_ frame: Data) async throws {
        let value = try HelperJSON.object(frame)
        context = value
        if value["origin"] as? String == "user", holdUser {
            userWriteHeld = true
            await withCheckedContinuation { writing = $0 }
        }
        if ended { throw HelperSessionFailure.unavailable }
        sent.append(frame)
        guard let id = value["id"] as? String, let kind = value["type"] as? String else { return }
        if kind == "player.state" {
            samples += 1
            if stationaryHandshake, samples == 2 {
                try emit("sync.identity", ["username": "Viewer_2"])
                try emit("sync.connection", ["state": "connected"])
            }
            return
        }
        var hello: [String: Any] = ["syncplayRevision": sessionRevision]
        if advertiseAdmission { hello["admissionVersion"] = 1 }
        var reply = base(kind == "bridge.hello" ? "bridge.ready" : "bridge.result", kind == "bridge.hello" ? hello : ["status": "applied"])
        if kind == "sync.connect", ticketMode {
            guard let payload = value["payload"] as? [String: Any], payload["accountAdmission"] as? Bool == true else { throw HelperSessionFailure.malformed }
            // Intentionally precedes connect acknowledgement: the native writer must not nest requests.
            try emit("sync.admissionRequired", ["nonce": String(repeating: "a", count: 32)])
        }
        if kind == "sync.setReady", pulseBeforeAck == true {
            try emit("sync.readiness", ["ready": true])
            try emit("sync.readiness", ["ready": false])
        }
        reply["replyTo"] = id
        try push(reply)
        if (kind == "sync.connect" && !ticketMode && !stationaryHandshake) || kind == "sync.admit" {
            try emit("sync.identity", ["username": "Viewer_2"])
            try emit("sync.connection", ["state": "connected"])
        }
        if kind == "sync.setReady", automaticReady, pulseBeforeAck != true {
            try emit("sync.readiness", ["ready": true])
            if pulseBeforeAck == false { try emit("sync.readiness", ["ready": false]) }
        }
    }
    func read() async throws -> Data {
        if !incoming.isEmpty { return incoming.removeFirst() }
        if ended { throw HelperSessionFailure.unavailable }
        return try await withCheckedThrowingContinuation { reading = $0 }
    }
    func close() {
        ended = true
        reading?.resume(throwing: HelperSessionFailure.unavailable)
        reading = nil
        writing?.resume()
        writing = nil
    }
    func configureAdmission(advertise: Bool = true) { ticketMode = true; advertiseAdmission = advertise }
    func requestAdmission(_ nonce: String) throws { try emit("sync.admissionRequired", ["nonce": nonce]) }
    func admissionCount() throws -> Int { try sent.filter { try HelperJSON.object($0)["type"] as? String == "sync.admit" }.count }
    func admissionMatches(_ index: Int, ticket: String, nonce: String) throws -> Bool {
        let frames = try sent.map { try HelperJSON.object($0) }.filter { $0["type"] as? String == "sync.admit" }
        guard frames.indices.contains(index), let payload = frames[index]["payload"] as? [String: Any] else { return false }
        return payload["nonce"] as? String == nonce && payload["ticket"] as? String == ticket && payload["clientRunId"] as? String == "member-run"
    }
    func readiness(_ value: Bool) throws { try emit("sync.readiness", ["ready": value]) }
    func connection(_ value: String) throws { try emit("sync.connection", ["state": value]) }
    func identity(_ value: String) throws { try emit("sync.identity", ["username": value]) }
    func requireStationaryHandshake() { stationaryHandshake = true }
    func holdReadiness() { automaticReady = false }
    func pulseReadiness(beforeAck: Bool) { pulseBeforeAck = beforeAck }
    func userMatches(position: Int64, paused: Bool) throws -> Bool {
        guard let frame = try sent.last(where: { try HelperJSON.object($0)["origin"] as? String == "user" }),
            let payload = try HelperJSON.object(frame)["payload"] as? [String: Any]
        else { return false }
        return payload["positionMs"] as? Int64 == position && payload["paused"] as? Bool == paused
    }
    func readyCount() throws -> Int {
        try sent.filter { try HelperJSON.object($0)["type"] as? String == "sync.setReady" }.count
    }
    func gateUserWrite() { holdUser = true }
    func releaseUserWrite() {
        holdUser = false
        writing?.resume()
        writing = nil
    }
    func correction(_ id: Int, after: Int, position: Int) throws {
        var value = base("player.setPosition", ["positionMs": position])
        value["id"] = "cmd-\(id)"
        value["afterUserAction"] = after
        try push(value)
    }
    func replyCount(_ id: Int, kind: String) throws -> Int {
        try sent.filter {
            let v = try HelperJSON.object($0)
            return v["replyTo"] as? String == "cmd-\(id)" && v["type"] as? String == kind
        }.count
    }
    func sawNativeEnd() throws -> Bool {
        try sent.contains {
            let value = try HelperJSON.object($0)
            let payload = value["payload"] as? [String: Any]
            return value["type"] as? String == "player.state" && payload?["positionMs"] as? Int == 60000
                && payload?["seekable"] as? Bool == false && payload?["paused"] as? Bool == true
        }
    }
    func userCount() throws -> Int {
        try sent.filter { try HelperJSON.object($0)["origin"] as? String == "user" }.count
    }
}

private actor SessionPlayer: PlaybackPort {
    let scope: PlaybackScope
    private var sequence: UInt64 = 1
    private(set) var paused = false
    private(set) var position: Int64 = 1200
    private(set) var seeks = 0
    private(set) var closes = 0
    private var ended = false
    init(_ scope: PlaybackScope) { self.scope = scope }
    func observe(_ scope: PlaybackScope) throws -> PlaybackObservation {
        try self.scope.check(clientRunID: scope.clientRunID, mediaGeneration: scope.mediaGeneration)
        return try PlaybackObservation(
            scope: scope, sequence: sequence, status: .ready, positionMs: position,
            durationMs: 60000, paused: paused, rate: 1, seekable: !ended, buffering: false, ended: ended,
            width: 0, height: 0, videoOutputs: 0, volume: nil, muted: false)
    }
    func finishMedia() {
        sequence += 1
        ended = true
        paused = true
        position = 60000
    }
    func setPaused(_ paused: Bool, scope: PlaybackScope) {
        if self.paused != paused { sequence += 1 }
        self.paused = paused
    }
    func seek(to positionMs: Int64, scope: PlaybackScope) throws {
        try Task.checkCancellation()
        sequence += 1
        position = positionMs
        seeks += 1
    }
    func load(_ localFile: URL, scope: PlaybackScope) throws { throw HelperSessionFailure.unavailable }
    func setRate(_ rate: Double, scope: PlaybackScope) throws { throw HelperSessionFailure.unavailable }
    func setVolume(_ volume: Double, muted: Bool, scope: PlaybackScope) throws {
        throw HelperSessionFailure.unavailable
    }
    func tracks(subtitles: Bool, scope: PlaybackScope) -> [PlaybackTrack] { [] }
    func selectTrack(_ id: Int32, subtitles: Bool, scope: PlaybackScope) throws {
        throw HelperSessionFailure.unavailable
    }
    func stop(_ scope: PlaybackScope) throws { throw HelperSessionFailure.unavailable }
    func close() { closes += 1 }
}

@MainActor private func eventually(_ predicate: () async throws -> Bool) async throws {
    let end = ContinuousClock.now.advanced(by: .seconds(3))
    while try await !predicate() {
        if ContinuousClock.now >= end { throw HelperSessionFailure.timeout }
        try await Task.sleep(for: .milliseconds(10))
    }
}

@Test @MainActor func sessionPublicationReplayAndFailure() async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision)
    try session.start()
    try await eventually { session.state.ready }
    #expect(session.state.acceptedUsername == "Viewer_2")
    #expect(await player.paused)
    #expect(try await peer.userCount() == 0)
    await peer.gateUserWrite()
    try session.pause(false)
    try await eventually { await peer.userWriteHeld }
    // It is valid for the server's correction to race completion of this write.
    try await peer.correction(1, after: 1, position: 2400)
    try await Task.sleep(for: .milliseconds(30))
    #expect(session.state.connection == .connected)
    #expect(await player.seeks == 0)
    await peer.releaseUserWrite()
    try await eventually { try await peer.replyCount(1, kind: "player.result") == 1 }
    try await peer.correction(1, after: 1, position: 2400)
    try await eventually { try await peer.replyCount(1, kind: "player.result") == 2 }
    #expect(await player.seeks == 1)
    try await peer.readiness(false)
    try await eventually { !session.state.ready }
    try session.seek(3000)
    try await eventually { try await peer.userCount() == 2 }
    try await peer.correction(2, after: 0, position: 0)
    try await eventually { try await peer.replyCount(2, kind: "bridge.error") == 1 }
    #expect(await player.position == 3000)
    #expect(try await peer.userCount() == 2)
    // An authority watermark that was never issued must fail closed.
    try await peer.correction(3, after: 3, position: 0)
    try await eventually { session.state.connection == .failed }
    await session.close()
    #expect(await player.paused)
    #expect(await player.closes == 0)
}

@Test @MainActor func helperEOFPausesBorrowedPlayer() async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision)
    try session.start()
    try await eventually { session.state.ready }
    try session.pause(false)
    try await eventually { try await peer.userCount() == 1 }
    await peer.close()
    try await eventually { session.state.connection == .failed }
    await session.close()
    await session.close()
    #expect(await player.paused)
    #expect(await player.closes == 0)
}

@Test @MainActor func roomIdentityCannotBeSilentlyRenamed() async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    await player.setPaused(false, scope: native)
    let peer = SessionPeer()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision, requireDeclaredIdentity: true)
    try session.start()
    try await eventually { session.state.connection == .failed }
    await session.close()
    await session.close()
    #expect(!session.state.ready)
    #expect(session.state.acceptedUsername == nil)
    #expect(session.state.identityRejected)
    #expect(try await peer.userCount() == 0)
    #expect(await player.paused)
    #expect(await player.closes == 0)
}

@Test @MainActor func nativeFileCompletionIsNotHelperFailure() async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision)
    do {
        try session.start()
        try await eventually { session.state.ready }
        await player.finishMedia()
        try await eventually { try await peer.sawNativeEnd() }
        #expect(session.state.connection == .connected && session.state.ready)
        #expect(try await peer.userCount() == 0)
        await session.close()
        #expect(await player.closes == 0)
    } catch {
        await session.close()
        throw error
    }
}

@Test(arguments: [
    "recover", "paused", "missingIdentity", "renamed", "earlyReady", "pendingIntent", "pendingCorrection", "cancel",
    "timeout",
])
@MainActor func sessionRecovery(_ scenario: String) async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer_2"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision, requireDeclaredIdentity: true)
    do {
        try session.start()
        try await eventually { session.state.ready }
        await peer.holdReadiness()
        if scenario == "pendingIntent" || scenario == "pendingCorrection" { await peer.gateUserWrite() }
        try session.pause(scenario == "paused")
        if scenario == "pendingIntent" || scenario == "pendingCorrection" {
            try await eventually { await peer.userWriteHeld }
            if scenario == "pendingIntent" {
                try session.seek(4000)
            } else {
                try await peer.correction(1, after: 1, position: 4000)
            }
            try await peer.connection("connecting")
            try await Task.sleep(for: .milliseconds(30))
            await peer.releaseUserWrite()
            try await eventually { session.state.connection == .failed }
            #expect(try await peer.userCount() == 1)
        } else {
            try await eventually { try await peer.userCount() == 1 }
            try await peer.connection("connecting")
            try await eventually { session.state.connection == .reconnecting && !session.state.ready }
            try await eventually { await player.paused }
            #expect(session.state.acceptedUsername == "Viewer_2")
            #expect(throws: (any Error).self) { try session.togglePause() }
            #expect(throws: (any Error).self) { try session.seek(4000) }
            switch scenario {
            case "missingIdentity":
                try await peer.connection("connected")
                try await eventually { session.state.connection == .failed }
            case "renamed":
                try await peer.identity("Viewer_3")
                try await eventually { session.state.connection == .failed }
                #expect(session.state.identityRejected)
            case "earlyReady":
                try await peer.readiness(true)
                try await eventually { session.state.connection == .failed }
            case "cancel":
                await session.close()
                #expect(session.state.connection == .stopped)
            case "timeout":
                try await Task.sleep(for: .seconds(20))
                try await peer.connection("connecting")
                try await Task.sleep(for: .milliseconds(5300))
                try await eventually { session.state.connection == .failed }
            default:
                for attempt in 2...3 {
                    try await peer.identity("Viewer_2")
                    try await peer.connection("connected")
                    try await eventually { try await peer.readyCount() == attempt }
                    #expect(session.state.connection == .reconnecting && !session.state.ready)
                    try await peer.readiness(true)
                    try await eventually { session.state.connection == .connected && session.state.ready }
                    try session.seek(Int64(attempt * 1000))
                    try await eventually { try await peer.userCount() == attempt }
                    if attempt == 2 {
                        try await peer.connection("connecting")
                        try await eventually { session.state.connection == .reconnecting }
                    }
                }
                #expect(await player.position == 3000)
            }
        }
        await session.close()
    } catch {
        await session.close()
        throw error
    }
    await session.close()
    #expect(await player.paused)
    #expect(await player.closes == 0)
}

@Test(arguments: [false, true]) @MainActor func readinessPulseRetainsAdmissionAndResumeIsOneIntent(_ beforeAck: Bool)
    async throws
{
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    await peer.pulseReadiness(beforeAck: beforeAck)
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision)
    do {
        try session.start()
        try await eventually { session.state.playbackReady && !session.state.ready }
        #expect(session.state.connection == .connected)
        #expect(throws: HelperSessionFailure.self) { try session.play(from: -1) }
        #expect(throws: HelperSessionFailure.self) { try session.play(from: 60001) }
        try session.play(from: 22000)
        try await eventually { try await peer.userCount() == 1 }
        #expect(try await peer.userMatches(position: 22000, paused: false))
        #expect(await player.position == 22000)
        #expect(await !player.paused)
        #expect(try await peer.readyCount() == 1)
        await session.close()
        #expect(!session.state.playbackReady)
        #expect(await player.closes == 0)
    } catch {
        await session.close()
        throw error
    }
}

@Test @MainActor func stationaryPlayerFinishesInitialHandshake() async throws {
    let native = try PlaybackScope.fresh().replacingMedia()
    let player = SessionPlayer(native)
    let peer = SessionPeer()
    await peer.requireStationaryHandshake()
    let session = try HelperSession(
        transport: peer, player: player, nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision)
    do {
        try session.start()
        try await eventually { session.state.playbackReady }
        #expect(await player.paused)
        #expect(await player.position == 1200)
        #expect(try await peer.userCount() == 0)
    } catch {
        await session.close()
        throw error
    }
    await session.close()
    #expect(await player.closes == 0)
}

@MainActor private final class AdmissionSource {
    var calls = 0
    var slow = false
    var cancelled = false
    func provide() async throws -> SyncplayAdmission {
        calls += 1
        if slow {
            do { try await Task.sleep(for: .seconds(20)) }
            catch { cancelled = true; throw error }
        }
        return try SyncplayAdmission(credential: String(repeating: calls == 1 ? "A" : "B", count: 64), clientRunID: "member-run")
    }
}

@Test @MainActor func helperAdmissionUsesFreshChallengesAndRejectsReplay() async throws {
    let native = try PlaybackScope.fresh().replacingMedia(), source = AdmissionSource()
    let peer = SessionPeer(); await peer.configureAdmission()
    let session = try HelperSession(transport: peer, player: SessionPlayer(native), nativeScope: native,
        endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer_2"),
        media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
        revision: sessionRevision, requireDeclaredIdentity: true, admissionProvider: source.provide)
    do {
        try session.start(); try await eventually { session.state.playbackReady }
        #expect(source.calls == 1)
        #expect(try await peer.admissionMatches(0, ticket: String(repeating: "A", count: 64), nonce: String(repeating: "a", count: 32)))
        try await peer.connection("connecting"); try await peer.requestAdmission(String(repeating: "b", count: 32))
        try await eventually { source.calls == 2 && session.state.playbackReady }
        #expect(try await peer.admissionMatches(1, ticket: String(repeating: "B", count: 64), nonce: String(repeating: "b", count: 32)))
        try await peer.connection("connecting"); try await peer.requestAdmission(String(repeating: "a", count: 32))
        try await eventually { session.state.connection == .failed }
        #expect(source.calls == 2)
    } catch { await session.close(); throw error }
    await session.close()
}

@Test @MainActor func helperAdmissionCloseJoinsCredentialWaitAndRequiresCapability() async throws {
    for missingCapability in [false, true] {
        let native = try PlaybackScope.fresh().replacingMedia(), source = AdmissionSource()
        source.slow = true
        let peer = SessionPeer(); await peer.configureAdmission(advertise: !missingCapability)
        let session = try HelperSession(transport: peer, player: SessionPlayer(native), nativeScope: native,
            endpoint: SyncplayEndpoint(host: "localhost", port: 8999, room: "room", username: "Viewer_2"),
            media: SharedMedia(handle: "media-1", name: "Movie.mkv", durationMs: 60000, fileSizeBytes: 1234),
            revision: sessionRevision, admissionProvider: source.provide)
        do {
            try session.start()
            try await eventually { missingCapability ? session.state.connection == .failed : source.calls == 1 }
            await session.close(); await session.close()
            #expect(source.calls == (missingCapability ? 0 : 1))
            #expect(missingCapability || source.cancelled)
            #expect(try await peer.admissionCount() == 0)
        } catch { await session.close(); throw error }
    }
    #expect(String(describing: try SyncplayAdmission(credential: String(repeating: "A", count: 64), clientRunID: "member-run")) == "[redacted]")
}
