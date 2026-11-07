import Foundation
import SyncWatchDomain

/// Command encoding only. The session owner serializes writes and allocates IDs.
public struct HelperCommands: Sendable {
    public let wireScope: PlaybackScope
    public init(wireScope: PlaybackScope) { self.wireScope = wireScope }

    public func hello(_ id: UInt64) throws -> Data {
        try frame(id, "bridge.hello", ["supportedVersions": [1]])
    }
    public func shutdown(_ id: UInt64) throws -> Data { try frame(id, "bridge.shutdown", [:]) }
    public func disconnect(_ id: UInt64) throws -> Data { try frame(id, "sync.disconnect", [:]) }
    public func ready(_ id: UInt64, value: Bool) throws -> Data { try frame(id, "sync.setReady", ["ready": value]) }
    public func connect(
        _ id: UInt64, host: String, port: Int, room: String, username: String, accountAdmission: Bool = false
    ) throws -> Data {
        guard (1...253).contains(host.utf8.count),
            host.range(of: "\\A[A-Za-z0-9.:_-]+\\z", options: .regularExpression) != nil,
            (1...65_535).contains(port)
        else { throw PlayerCorrectionFailure.malformed }
        try text(room, maximum: 256)
        try text(username, maximum: 64)
        var payload: [String: Any] = ["host": host, "port": port, "room": room, "username": username, "tls": true]
        if accountAdmission { payload["accountAdmission"] = true }
        return try frame(id, "sync.connect", payload)
    }
    public func admit(_ id: UInt64, nonce: String, authority: SyncplayAdmission) throws -> Data {
        guard nonce.range(of: "\\A[0-9a-f]{32}\\z", options: .regularExpression) != nil else {
            throw PlayerCorrectionFailure.malformed
        }
        return try frame(
            id, "sync.admit", ["nonce": nonce, "ticket": authority.credential, "clientRunId": authority.clientRunID])
    }
    public func loaded(_ id: UInt64, handle: String, name: String, durationMs: Int64, fileSizeBytes: UInt64) throws
        -> Data
    {
        _ = try PlaybackScope(clientRunID: handle, mediaGeneration: 0)
        try text(name, maximum: 256)
        guard !name.contains("/"), !name.contains("\\"), (0...WatchSession.maximumPositionMs).contains(durationMs),
            fileSizeBytes <= PlaybackScope.maximumGeneration
        else { throw PlayerCorrectionFailure.malformed }
        return try frame(
            id, "player.loaded",
            [
                "mediaHandle": handle, "name": name, "durationMs": durationMs, "fileSizeBytes": fileSizeBytes,
            ])
    }
    public func observation(_ id: UInt64, value: PlaybackObservation, nativeScope: PlaybackScope) throws -> Data {
        try state(id, value: value, nativeScope: nativeScope, userSequence: nil)
    }
    /// Call only for an explicit local shared action, after observing the player.
    public func userObservation(
        _ id: UInt64, sequence: UInt64, value: PlaybackObservation, nativeScope: PlaybackScope
    ) throws -> Data {
        guard sequence > 0, sequence <= PlaybackScope.maximumGeneration, value.status == .ready else {
            throw PlayerCorrectionFailure.malformed
        }
        return try state(id, value: value, nativeScope: nativeScope, userSequence: sequence)
    }
    private func state(
        _ id: UInt64, value: PlaybackObservation, nativeScope: PlaybackScope, userSequence: UInt64?
    ) throws -> Data {
        try nativeScope.check(clientRunID: value.scope.clientRunID, mediaGeneration: value.scope.mediaGeneration)
        let status = ["unloaded", "loading", "ready", "failed"][Int(value.status.rawValue)]
        return try frame(
            id, "player.state",
            [
                "status": status, "positionMs": value.positionMs,
                "durationMs": value.durationMs.map { $0 as Any } ?? NSNull(),
                "paused": value.paused, "rate": value.rate, "seekable": value.seekable,
            ], userSequence: userSequence)
    }
    private func frame(_ id: UInt64, _ type: String, _ payload: [String: Any], userSequence: UInt64? = nil) throws
        -> Data
    {
        guard id > 0, id <= PlaybackScope.maximumGeneration else { throw PlayerCorrectionFailure.malformed }
        var value: [String: Any] = [
            "v": 1, "id": "cmd-\(id)", "type": type, "clientRunId": wireScope.clientRunID,
            "mediaGeneration": wireScope.mediaGeneration, "payload": payload,
        ]
        if type == "player.state" { value["origin"] = userSequence == nil ? "observation" : "user" }
        if let userSequence { value["actionSequence"] = userSequence }
        var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        data.append(10)
        guard data.count <= 65_536 else { throw PlayerCorrectionFailure.malformed }
        return data
    }
    private func text(_ value: String, maximum: Int) throws {
        guard (1...maximum).contains(value.unicodeScalars.count),
            value.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value != 127 })
        else { throw PlayerCorrectionFailure.malformed }
    }
}
