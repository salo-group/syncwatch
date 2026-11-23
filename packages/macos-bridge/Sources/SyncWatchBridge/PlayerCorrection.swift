import CoreFoundation
import Foundation
import SyncWatchDomain

public enum PlayerCorrectionFailure: Error { case malformed, unavailable, unauthorizedHandle }

/// One validated helper request. The wire scope and the trusted native scope are
/// deliberately separate: restarting a helper must not replace the native owner.
public struct PlayerCorrection: Sendable, Equatable {
    public enum Operation: Sendable, Equatable {
        case pause(Bool)
        case seek(Int64)
        case rate(Double)
        case keepLoaded(String)
    }
    public let id: String
    public let afterUserAction: UInt64
    public let operation: Operation
    private let wireScope: PlaybackScope

    public static func decode(_ frame: Data, wireScope: PlaybackScope) throws -> Self {
        let value = try HelperJSON.object(frame)
        try HelperJSON.keys(
            value, required: ["v", "id", "type", "clientRunId", "mediaGeneration", "payload"],
            optional: ["afterUserAction"])
        guard try HelperJSON.integer(value["v"], maximum: 1) == 1,
            let run = value["clientRunId"] as? String, let id = value["id"] as? String,
            id.range(of: "\\Acmd-[1-9][0-9]{0,15}\\z", options: .regularExpression) != nil,
            let payload = value["payload"] as? [String: Any]
        else { throw PlayerCorrectionFailure.malformed }
        try wireScope.check(
            clientRunID: run,
            mediaGeneration: HelperJSON.integer(value["mediaGeneration"], maximum: PlaybackScope.maximumGeneration))
        let watermark =
            try value["afterUserAction"].map {
                try HelperJSON.integer($0, maximum: PlaybackScope.maximumGeneration)
            } ?? 0
        let operation: Operation
        switch value["type"] as? String {
        case "player.setPaused":
            try HelperJSON.keys(payload, required: ["paused"])
            guard let number = payload["paused"] as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else {
                throw PlayerCorrectionFailure.malformed
            }
            operation = .pause(number.boolValue)
        case "player.setPosition":
            try HelperJSON.keys(payload, required: ["positionMs"])
            operation = .seek(Int64(try HelperJSON.integer(payload["positionMs"], maximum: 604_800_000)))
        case "player.setSpeed":
            try HelperJSON.keys(payload, required: ["rate"])
            let rate = try HelperJSON.number(payload["rate"])
            guard (0.25...4).contains(rate) else { throw PlayerCorrectionFailure.malformed }
            operation = .rate(rate)
        case "player.openFile":
            try HelperJSON.keys(payload, required: ["mediaHandle"])
            guard let handle = payload["mediaHandle"] as? String else { throw PlayerCorrectionFailure.malformed }
            _ = try PlaybackScope(clientRunID: handle, mediaGeneration: 0)
            operation = .keepLoaded(handle)
        default: throw PlayerCorrectionFailure.malformed
        }
        return Self(id: id, afterUserAction: watermark, operation: operation, wireScope: wireScope)
    }

    /// The coordinator checks its action fence before execution and again after
    /// this await. A submitted VLC command is not an acknowledgement of its target.
    public func apply(
        to player: any PlaybackPort, nativeScope: PlaybackScope, authorizedHandle: String
    ) async throws -> PlaybackObservation {
        try Task.checkCancellation()
        switch operation {
        case .pause(let paused): try await player.setPaused(paused, scope: nativeScope)
        case .seek(let position): try await player.seek(to: position, scope: nativeScope)
        case .rate(let rate): try await player.setRate(rate, scope: nativeScope)
        case .keepLoaded(let handle):
            guard handle == authorizedHandle else { throw PlayerCorrectionFailure.unauthorizedHandle }
        // Selection already owns this loaded file. Never reopen or rewind it.
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while true {
            try Task.checkCancellation()
            let observation = try await player.observe(nativeScope)
            try validate(observation, nativeScope: nativeScope)
            if case .pause(let expected) = operation, observation.paused != expected {
                guard ContinuousClock.now < deadline else { throw PlayerCorrectionFailure.unavailable }
                try await Task.sleep(for: .milliseconds(20))
            } else {
                return observation
            }
        }
    }

    public func result(_ observation: PlaybackObservation, nativeScope: PlaybackScope) throws -> Data {
        try validate(observation, nativeScope: nativeScope)
        return try reply(
            "player.result",
            [
                "status": "applied", "positionMs": observation.positionMs, "paused": observation.paused,
            ])
    }

    /// Only send after the newer actual user frame has been published successfully.
    public func cancelledReply() throws -> Data {
        try reply("bridge.error", ["code": "cancelled", "message": "cancelled", "retryable": false])
    }

    private func validate(_ value: PlaybackObservation, nativeScope: PlaybackScope) throws {
        try nativeScope.check(clientRunID: value.scope.clientRunID, mediaGeneration: value.scope.mediaGeneration)
        guard value.status == .ready else { throw PlayerCorrectionFailure.unavailable }
    }
    private func reply(_ type: String, _ payload: [String: Any]) throws -> Data {
        var data = try JSONSerialization.data(
            withJSONObject: [
                "v": 1, "replyTo": id, "type": type, "clientRunId": wireScope.clientRunID,
                "mediaGeneration": wireScope.mediaGeneration, "payload": payload,
            ], options: [.sortedKeys])
        data.append(10)
        return data
    }
}

enum HelperJSON {
    static func keys(_ value: [String: Any], required: Set<String>, optional: Set<String> = []) throws {
        let keys = Set(value.keys)
        guard required.isSubset(of: keys), keys.isSubset(of: required.union(optional)) else {
            throw PlayerCorrectionFailure.malformed
        }
    }
    static func number(_ value: Any?) throws -> Double {
        guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite
        else {
            throw PlayerCorrectionFailure.malformed
        }
        return value.doubleValue
    }
    static func integer(_ value: Any?, maximum: UInt64) throws -> UInt64 {
        let value = try number(value)
        guard value >= 0, value <= Double(maximum), value.rounded(.towardZero) == value else {
            throw PlayerCorrectionFailure.malformed
        }
        return UInt64(value)
    }
    static func object(_ frame: Data) throws -> [String: Any] {
        guard (2...65_536).contains(frame.count), !frame.starts(with: [239, 187, 191]),
            frame.last == 10, !frame.dropLast().contains(10),
            !frame.contains(13), String(data: frame, encoding: .utf8) != nil
        else { throw PlayerCorrectionFailure.malformed }
        // Foundation collapses duplicate keys. Reject them before conversion,
        // including escaped aliases such as "id" and "\u0069d".
        let bytes = Array(frame)
        var stack: [Set<String>] = []
        var index = 0
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 123 || byte == 91 {
                stack.append([])
                guard stack.count <= 32 else { throw PlayerCorrectionFailure.malformed }
            } else if byte == 125 || byte == 93 {
                guard !stack.isEmpty else { throw PlayerCorrectionFailure.malformed }
                stack.removeLast()
            } else if byte == 34 {
                let start = index
                index += 1
                while index < bytes.count && bytes[index] != 34 {
                    if bytes[index] == 92 { index += 1 }
                    index += 1
                }
                guard index < bytes.count else { throw PlayerCorrectionFailure.malformed }
                var next = index + 1
                while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                if next < bytes.count && bytes[next] == 58 {
                    guard !stack.isEmpty,
                        let key = try JSONSerialization.jsonObject(
                            with: Data(bytes[start...index]), options: [.fragmentsAllowed]) as? String,
                        stack[stack.count - 1].insert(key).inserted
                    else { throw PlayerCorrectionFailure.malformed }
                }
            }
            index += 1
        }
        guard stack.isEmpty, let value = try JSONSerialization.jsonObject(with: frame) as? [String: Any] else {
            throw PlayerCorrectionFailure.malformed
        }
        return value
    }
}
