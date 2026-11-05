import Foundation
import SyncWatchDomain

/// Single-transport authority. Neither diagnostic description exposes its credential.
public struct SyncplayAdmission: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    let credential: String
    let clientRunID: String
    public init(credential: String, clientRunID: String) throws {
        guard credential.range(of: "\\A[0-9A-F]{64}\\z", options: .regularExpression) != nil else {
            throw HelperSessionFailure.malformed
        }
        _ = try PlaybackScope(clientRunID: clientRunID, mediaGeneration: 0)
        self.credential = credential; self.clientRunID = clientRunID
    }
    public var description: String { "[redacted]" }
    public var debugDescription: String { description }
}
