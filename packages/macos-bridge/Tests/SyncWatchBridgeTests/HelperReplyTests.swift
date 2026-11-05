import Foundation
import SyncWatchDomain
import Testing

@testable import SyncWatchBridge

@Test func sharedCorrelatedHelperReplies() throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_CONTRACT_FIXTURES"])
    let url = URL(filePath: path).deletingLastPathComponent().appendingPathComponent("helper-replies.json")
    let corpus = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let cases = try #require(corpus["cases"] as? [[String: Any]])
    #expect(cases.count == 40)
    let scope = try PlaybackScope(clientRunID: "helper-run", mediaGeneration: 7)
    for fixture in cases {
        let expected: HelperReplyKind =
            fixture["expectedKind"] as? String == "ready"
            ? .ready : fixture["expectedKind"] as? String == "stopped" ? .stopped : .applied
        var accepted = false
        do {
            let reply = try HelperReply.decode(
                Data((fixture["frame"] as! String).utf8), wireScope: scope, commandID: 9, expected: expected,
                expectedRevision: fixture["expectedRevision"] as? String, requireAdmission: fixture["requireAdmission"] as? Bool == true)
            if fixture["name"] as? String == "rejected" {
                #expect(reply == .rejected(code: "unavailable", retryable: true))
            }
            accepted = true
        } catch {}
        #expect(accepted == fixture["accepted"] as? Bool, "\(fixture["name"] ?? "")")
    }
}
