import Foundation
import SyncWatchDomain
import Testing

@testable import SyncWatchBridge

@Test func sharedOutgoingHelperCommands() throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_CONTRACT_FIXTURES"])
    let url = URL(filePath: path).deletingLastPathComponent().appendingPathComponent("helper-commands.json")
    let corpus = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let cases = try #require(corpus["cases"] as? [[String: Any]])
    #expect(cases.count == 31)
    let encoder = HelperCommands(wireScope: try PlaybackScope(clientRunID: "helper-run", mediaGeneration: 7))
    let native = try PlaybackScope(clientRunID: "native-owner", mediaGeneration: 2)
    for fixture in cases {
        let args = try #require(fixture["args"] as? [String: Any])
        let id = args["id"] as? UInt64 ?? 9
        var encoded: NSDictionary?
        do {
            let frame: Data
            switch fixture["op"] as? String {
            case "hello": frame = try encoder.hello(id)
            case "shutdown": frame = try encoder.shutdown(id)
            case "disconnect": frame = try encoder.disconnect(id)
            case "ready": frame = try encoder.ready(id, value: true)
            case "loaded":
                frame = try encoder.loaded(
                    id, handle: args["handle"] as? String ?? "media-1", name: args["name"] as? String ?? "Кино 🎬.mkv",
                    durationMs: args["durationMs"] as? Int64 ?? 4000,
                    fileSizeBytes: args["fileSizeBytes"] as? UInt64 ?? 4096)
            case "connect":
                frame = try encoder.connect(
                    id, host: args["host"] as? String ?? "127.0.0.1", port: args["port"] as? Int ?? 8999,
                    room: args["room"] as? String ?? "room", username: args["username"] as? String ?? "Viewer",
                    accountAdmission: args["accountAdmission"] as? Bool == true)
            case "admit":
                frame = try encoder.admit(
                    id, nonce: args["nonce"] as? String ?? String(repeating: "a", count: 32),
                    authority: SyncplayAdmission(
                        credential: args["ticket"] as? String ?? String(repeating: "A", count: 64),
                        clientRunID: args["clientRunId"] as? String ?? "room-run"))
            case "observation", "user":
                let owner =
                    args["foreignNative"] as? Bool == true
                    ? try PlaybackScope(clientRunID: "foreign", mediaGeneration: 2) : native
                let loading = args["loading"] as? Bool == true
                let actual = try PlaybackObservation(
                    scope: owner, sequence: 1, status: loading ? .loading : .ready,
                    positionMs: 1300, durationMs: loading ? nil : 4000, paused: true, rate: 1,
                    seekable: !loading, buffering: false, ended: false, width: 0, height: 0, videoOutputs: 0,
                    volume: 0.25, muted: true)
                frame =
                    try fixture["op"] as? String == "user"
                    ? encoder.userObservation(
                        id, sequence: args["sequence"] as? UInt64 ?? 3, value: actual, nativeScope: native)
                    : encoder.observation(id, value: actual, nativeScope: native)
            default: throw PlayerCorrectionFailure.malformed
            }
            #expect(frame.last == 10 && frame.count <= 65_536)
            encoded = try JSONSerialization.jsonObject(with: frame) as? NSDictionary
        } catch {}
        #expect(encoded == fixture["expected"] as? NSDictionary, "\(fixture["name"] ?? "")")
    }
}
