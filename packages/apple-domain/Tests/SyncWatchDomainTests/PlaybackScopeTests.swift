import Foundation
import Testing

@testable import SyncWatchDomain

@Test func sharedRunAndGenerationFixtures() throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_CONTRACT_FIXTURES"])
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    let cases = try #require(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
    let names: Set = ["playback-ready", "obsolete-media", "unallocated-media", "replaced-helper"]
    var tested = Set<String>()
    for fixture in cases {
        guard let name = fixture["name"] as? String, names.contains(name) else { continue }
        tested.insert(name)
        let value = try #require(fixture["value"] as? [String: Any])
        let context = try #require(fixture["context"] as? [String: Any])
        let scope = try PlaybackScope(
            clientRunID: #require(context["clientRunId"] as? String),
            mediaGeneration: #require(context["mediaGeneration"] as? UInt64)
        )
        var rejection: ContractRejection?
        do {
            try scope.check(
                clientRunID: #require(value["clientRunId"] as? String),
                mediaGeneration: #require(value["mediaGeneration"] as? UInt64)
            )
        } catch let error as ContractRejection {
            rejection = error
        }
        #expect(rejection?.rawValue == fixture["error"] as? String, "Fixture: \(name)")
    }
    #expect(tested == names)
}

@Test func replacingMediaInvalidatesPendingWork() throws {
    let original = try PlaybackScope(clientRunID: "run-7", mediaGeneration: 3)
    let replacement = try original.replacingMedia()
    #expect(replacement.clientRunID == original.clientRunID)
    #expect(throws: ContractRejection.staleGeneration) {
        try replacement.check(clientRunID: original.clientRunID, mediaGeneration: original.mediaGeneration)
    }
    try replacement.check(clientRunID: original.clientRunID, mediaGeneration: 4)
}

@Test func restartingCannotReuseAnOldProcessScope() throws {
    let original = PlaybackScope.fresh()
    let replacement = PlaybackScope.fresh()
    #expect(throws: ContractRejection.foreignRun) {
        try replacement.check(clientRunID: original.clientRunID, mediaGeneration: 0)
    }
}

@Test func generationCannotOverflowOrExceedWirePrecision() throws {
    let last = try PlaybackScope(clientRunID: "run-7", mediaGeneration: PlaybackScope.maximumGeneration)
    #expect(throws: ContractRejection.malformed) { try last.replacingMedia() }
    #expect(throws: ContractRejection.malformed) {
        try PlaybackScope(clientRunID: "run-7", mediaGeneration: UInt64.max)
    }
}

@Test(arguments: ["", "-run", "run/7", "run\n7", "\u{00e9}", String(repeating: "a", count: 65)])
func invalidProcessIdentitiesAreRejected(identifier: String) {
    #expect(throws: ContractRejection.malformed) {
        try PlaybackScope(clientRunID: identifier, mediaGeneration: 0)
    }
}
