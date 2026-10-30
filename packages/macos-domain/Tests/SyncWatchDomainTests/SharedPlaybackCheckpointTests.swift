import Foundation
import Testing

@testable import SyncWatchDomain

@Test func sharedPlaybackCheckpointCorpus() throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_CONTRACT_FIXTURES"])
    let url = URL(fileURLWithPath: path).deletingLastPathComponent()
        .appendingPathComponent("shared-playback-checkpoint.json")
    let corpus = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let cases = try #require(corpus["cases"] as? [[String: Any]])
    #expect(cases.count == 18)
    let scope = try PlaybackScope(clientRunID: "run-1", mediaGeneration: 2)
    for row in cases {
        var model = SharedPlaybackCheckpoint(scope: scope)
        for step in try #require(row["steps"] as? [[String: Any]]) {
            switch step["op"] as? String {
            case "interrupt": model.interrupt()
            case "clear": model.clear()
            case "observe":
                let owner = try PlaybackScope(
                    clientRunID: step["scope"] as? String == "foreignRun" ? "run-2" : "run-1",
                    mediaGeneration: step["scope"] as? String == "oldGeneration" ? 1 : 2)
                let status: PlaybackStatus =
                    step["status"] as? String == "failed"
                    ? .failed
                    : step["status"] as? String == "loading" ? .loading : .ready
                let observation = try PlaybackObservation(
                    scope: owner, sequence: step["sequence"] as? UInt64 ?? 1, status: status,
                    positionMs: step["positionMs"] as? Int64 ?? 49_000,
                    durationMs: step["durationMs"] is NSNull ? nil : step["durationMs"] as? Int64 ?? 60_000,
                    paused: step["paused"] as? Bool ?? true, rate: 1,
                    seekable: step["seekable"] as? Bool ?? true,
                    buffering: step["buffering"] as? Bool ?? false, ended: step["ended"] as? Bool ?? false,
                    width: 0, height: 0, videoOutputs: 0, volume: nil, muted: false)
                model.observe(observation, available: step["available"] as? Bool ?? true)
            default: Issue.record("Unknown checkpoint fixture operation")
            }
        }
        #expect(model.position(durationMs: 60_000) == row["expectedPositionMs"] as? Int64, "\(row["name"]!)")
        #expect(model.position(durationMs: 90_000) == nil)
    }
}
