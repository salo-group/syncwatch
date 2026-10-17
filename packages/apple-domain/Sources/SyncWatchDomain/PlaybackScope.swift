import Foundation

public enum ContractRejection: String, Error, Sendable {
    case malformed
    case foreignRun
    case staleGeneration
}

/// Trusted ownership of the current local media, independent of UI and playback engines.
public struct PlaybackScope: Equatable, Sendable {
    public static let maximumGeneration: UInt64 = 9_007_199_254_740_991
    public let clientRunID: String
    public let mediaGeneration: UInt64

    public init(clientRunID: String, mediaGeneration: UInt64) throws {
        let bytes = Array(clientRunID.utf8)
        func isAlphanumeric(_ byte: UInt8) -> Bool {
            (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte)
        }
        guard (1...64).contains(bytes.count),
            let first = bytes.first, isAlphanumeric(first),
            bytes.allSatisfy({ isAlphanumeric($0) || [45, 46, 95].contains($0) }),
            mediaGeneration <= Self.maximumGeneration
        else {
            throw ContractRejection.malformed
        }
        self.clientRunID = clientRunID
        self.mediaGeneration = mediaGeneration
    }

    private init(freshRunID: String) {
        clientRunID = freshRunID
        mediaGeneration = 0
    }

    public static func fresh() -> Self {
        Self(freshRunID: UUID().uuidString)
    }

    public func check(clientRunID: String, mediaGeneration: UInt64) throws {
        let incoming = try Self(clientRunID: clientRunID, mediaGeneration: mediaGeneration)
        guard incoming.clientRunID == self.clientRunID else {
            throw ContractRejection.foreignRun
        }
        guard incoming.mediaGeneration == self.mediaGeneration else {
            throw ContractRejection.staleGeneration
        }
    }

    public func replacingMedia() throws -> Self {
        guard mediaGeneration < Self.maximumGeneration else {
            throw ContractRejection.malformed
        }
        return try Self(clientRunID: clientRunID, mediaGeneration: mediaGeneration + 1)
    }
}
