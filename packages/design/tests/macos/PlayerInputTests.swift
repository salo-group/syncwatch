import Foundation
import Testing

@testable import SyncWatchDesign

private struct InputEvent: Decodable {
    let action: String
    let value: Double?
}
private struct InputFixture: Decodable {
    let name: String
    let kind: String
    let key: String?
    let wheel: Double?
    let state: String?
    let positionMs: Int64?
    let volume: Double?
    let skipSeconds: Int?
    let enabledActions: [String]?
    let repeating: Bool?
    let fullscreen: Bool?
    let modified: Bool?
    let protectedFocus: Bool?
    let handled: Bool
    let expected: [InputEvent]
}
private struct InputDefaults: Decodable {
    let state: String
    let positionMs: Int64
    let durationMs: Int64
    let volume: Double
    let skipSeconds: Int
    let enabledActions: [String]
}
private struct InputCorpus: Decodable {
    let defaults: InputDefaults
    let cases: [InputFixture]
}

@Test func playerInputRespectsCapabilitiesNativeFocusRepeatAndLocalVolume() throws {
    let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("fixtures/player-input.json")
    let corpus = try JSONDecoder().decode(InputCorpus.self, from: Data(contentsOf: source))
    #expect(corpus.cases.count == 34)
    for row in corpus.cases {
        var model = PlayerPresentation()
        model.state = try #require(PlayerVisualState(rawValue: row.state ?? corpus.defaults.state))
        model.positionMs = row.positionMs ?? corpus.defaults.positionMs
        model.durationMs = corpus.defaults.durationMs
        model.volume = row.volume ?? corpus.defaults.volume
        model.skipSeconds = row.skipSeconds ?? corpus.defaults.skipSeconds
        model.enabledActions = Set(row.enabledActions ?? corpus.defaults.enabledActions)
        var received: [(String, Double?)] = []
        let handler: (String, Double?) -> Void = { received.append(($0, $1)) }
        let handled =
            row.kind == "wheel"
            ? model.dispatchWheel(
                row.wheel ?? 0, modified: row.modified ?? false, protectedFocus: row.protectedFocus ?? false,
                to: handler)
            : model.dispatchKey(
                row.key ?? "", repeating: row.repeating ?? false, fullscreen: row.fullscreen ?? false,
                modified: row.modified ?? false, protectedFocus: row.protectedFocus ?? false, to: handler)
        #expect(handled == row.handled, "\(row.name)")
        #expect(received.count == row.expected.count, "\(row.name)")
        for (actual, expected) in zip(received, row.expected) {
            #expect(actual.0 == expected.action, "\(row.name)")
            if let value = actual.1, let expectedValue = expected.value {
                #expect(abs(value - expectedValue) < 0.000_001, "\(row.name)")
            } else {
                #expect(actual.1 == nil && expected.value == nil, "\(row.name)")
            }
        }
        #expect(model.volume == (row.volume ?? corpus.defaults.volume))
        #expect(model.positionMs == (row.positionMs ?? corpus.defaults.positionMs))
    }
    let ready = PlayerPresentation.gallery(.paused)
    for value in [Double.nan, .infinity, -.infinity] {
        #expect(!ready.dispatchWheel(value) { _, _ in Issue.record("Nonfinite wheel emitted input") })
    }
}
