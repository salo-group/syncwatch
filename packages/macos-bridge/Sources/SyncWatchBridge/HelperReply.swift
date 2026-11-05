import CoreFoundation
import Foundation
import SyncWatchDomain

public enum HelperReplyKind: Sendable { case ready, applied, stopped }

public enum HelperReply: Sendable, Equatable {
    case ready(revision: String)
    case applied, stopped
    case rejected(code: String, retryable: Bool)

    /// A reply cannot acknowledge a different pending command or engine revision.
    public static func decode(
        _ frame: Data, wireScope: PlaybackScope, commandID: UInt64, expected: HelperReplyKind,
        expectedRevision: String? = nil, requireAdmission: Bool = false
    ) throws -> Self {
        guard commandID > 0, commandID <= PlaybackScope.maximumGeneration else {
            throw PlayerCorrectionFailure.malformed
        }
        let value = try HelperJSON.object(frame)
        try HelperJSON.keys(value, required: ["v", "replyTo", "type", "clientRunId", "mediaGeneration", "payload"])
        guard try HelperJSON.integer(value["v"], maximum: 1) == 1,
            value["replyTo"] as? String == "cmd-\(commandID)", let run = value["clientRunId"] as? String,
            let payload = value["payload"] as? [String: Any]
        else { throw PlayerCorrectionFailure.malformed }
        try wireScope.check(
            clientRunID: run,
            mediaGeneration: HelperJSON.integer(value["mediaGeneration"], maximum: PlaybackScope.maximumGeneration))
        let kind = value["type"] as? String
        if kind == "bridge.error" {
            try HelperJSON.keys(payload, required: ["code", "message", "retryable"])
            guard let code = payload["code"] as? String, errorCodes.contains(code),
                let message = payload["message"] as? String, (1...512).contains(message.unicodeScalars.count),
                message.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value != 127 }),
                let retryable = payload["retryable"] as? NSNumber, CFGetTypeID(retryable) == CFBooleanGetTypeID()
            else { throw PlayerCorrectionFailure.malformed }
            // Provider text can contain private metadata. Retain only the typed code.
            return .rejected(code: code, retryable: retryable.boolValue)
        }
        switch expected {
        case .ready:
            try HelperJSON.keys(payload, required: ["syncplayRevision"], optional: ["admissionVersion"])
            if let capability = payload["admissionVersion"] {
                guard try HelperJSON.integer(capability, maximum: 1) == 1 else { throw PlayerCorrectionFailure.malformed }
            } else if requireAdmission { throw PlayerCorrectionFailure.malformed }
            guard kind == "bridge.ready", let revision = expectedRevision,
                revision.range(of: "\\A[0-9a-f]{40}\\z", options: .regularExpression) != nil,
                payload["syncplayRevision"] as? String == revision
            else { throw PlayerCorrectionFailure.malformed }
            return .ready(revision: revision)
        case .applied:
            try HelperJSON.keys(payload, required: ["status"])
            guard kind == "bridge.result", payload["status"] as? String == "applied" else {
                throw PlayerCorrectionFailure.malformed
            }
            return .applied
        case .stopped:
            try HelperJSON.keys(payload, required: [])
            guard kind == "bridge.stopped" else { throw PlayerCorrectionFailure.malformed }
            return .stopped
        }
    }
    private static let errorCodes: Set<String> = [
        "malformed", "frameTooLarge", "unsupportedVersion", "foreignRun", "staleGeneration", "staleEpoch",
        "sequenceGap", "duplicate", "conflict", "invalidState", "queueFull", "unavailable", "timeout", "cancelled",
        "internal",
    ]
}
