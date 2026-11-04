import Foundation
import Testing

@testable import SyncWatchDomain

@Test func localVolumeCheckpointCorpus() throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_CONTRACT_FIXTURES"])
    let url = URL(fileURLWithPath: path).deletingLastPathComponent()
        .appendingPathComponent("local-volume-checkpoint.json")
    let corpus = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let cases = try #require(corpus["cases"] as? [[String: Any]])
    #expect(cases.count == 9)
    for row in cases {
        var model = LocalVolumeCheckpoint(scope: try PlaybackScope(clientRunID: "run-1", mediaGeneration: 2))
        for step in try #require(row["steps"] as? [[String: Any]]) {
            if step["op"] as? String == "submitted" { model.submitted(); continue }
            let owner = try PlaybackScope(
                clientRunID: step["owner"] as? String == "foreignRun" ? "run-2" : "run-1",
                mediaGeneration: step["owner"] as? String == "oldGeneration" ? 1 : 2)
            let value = try PlaybackObservation(
                scope: owner, sequence: try #require(step["sequence"] as? UInt64),
                status: step["status"] as? String == "loading" ? .loading : step["status"] as? String == "failed" ? .failed : .ready,
                positionMs: 0, durationMs: 60_000, paused: true, rate: 1,
                seekable: false, buffering: false, ended: false, width: 0, height: 0, videoOutputs: 0,
                volume: step["volume"] as? Double, muted: step["muted"] as? Bool ?? false)
            let actual = model.observe(value)
            #expect(actual == step["expected"] as? Double, "\(row["name"]!)")
        }
    }
}
