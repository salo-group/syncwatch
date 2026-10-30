import AppKit
import SwiftUI
import Testing

@testable import SyncWatchDesign

private struct PlayerEvent: Codable, Equatable {
    let action: String
    let value: Double?
}
private struct PlayerCase: Decodable {
    let name: String
    let state: String
    let sharedPositionMs: Int64?
    let positionMs: Int64
    let durationMs: Int64
    let volume: Double
    let skipSeconds: Int
    let hasPrevious: Bool
    let hasNext: Bool
    let enabledActions: [String]
    let requests: [PlayerEvent]
    let expected: [PlayerEvent]
}
private struct PlayerCases: Decodable { let cases: [PlayerCase] }

@Test func playerActionsRespectCapabilitiesBoundsAndObservationOwnership() throws {
    let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("fixtures/player-actions.json")
    let cases = try JSONDecoder().decode(PlayerCases.self, from: Data(contentsOf: source))
    #expect(cases.cases.count == 27)
    for fixture in cases.cases {
        var model = PlayerPresentation()
        model.state = try #require(PlayerVisualState(rawValue: fixture.state))
        model.sharedPositionMs = fixture.sharedPositionMs
        model.positionMs = fixture.positionMs
        model.durationMs = fixture.durationMs
        model.volume = fixture.volume
        model.skipSeconds = fixture.skipSeconds
        model.hasPrevious = fixture.hasPrevious
        model.hasNext = fixture.hasNext
        model.enabledActions = Set(fixture.enabledActions)
        var events: [PlayerEvent] = []
        for request in fixture.requests {
            model.dispatch(request.action, value: request.value) { events.append(PlayerEvent(action: $0, value: $1)) }
        }
        #expect(events == fixture.expected, "\(fixture.name)")
        #expect(model.positionMs == fixture.positionMs && model.state.rawValue == fixture.state)
        #expect((0...model.displayedDuration).contains(model.displayedPosition))
    }
    let ready = PlayerPresentation.gallery(.paused)
    var count = 0
    for value in [Double.nan, .infinity, -.infinity] {
        ready.dispatch("seek", value: value) { _, _ in count += 1 }
        ready.dispatch("setMovieVolume", value: value) { _, _ in count += 1 }
    }
    var invalid = ready
    invalid.volume = .nan
    #expect(!invalid.allows("muteMovie") && !invalid.allows("seek"))
    #expect(count == 0)
    #expect(PlayerPresentation.time(754_000) == "12:34")
    #expect(PlayerPresentation.time(5_400_000) == "1:30:00")
    #expect(PlayerPresentation.time(.nan) == "00:00")
    #expect(PlayerVisualState.allCases.map(\.rawValue) == SyncWatchPlayer.states)
}

/// Render only our unshown hosting windows; no desktop capture or activation.
@MainActor @Test func renderPlayerStateThemeLanguageAndViewportMatrix() throws {
    NSApplication.shared.setActivationPolicy(.prohibited)
    let output = ProcessInfo.processInfo.environment["SYNCWATCH_DESIGN_SCREENSHOTS"].map {
        URL(fileURLWithPath: $0, isDirectory: true)
    }
    if let output { try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true) }
    for state in PlayerVisualState.allCases {
        for (name, scheme) in [("dark", ColorScheme.dark), ("light", ColorScheme.light)] {
            for language in ["en", "ru"] {
                for (width, height) in [(1120, 760), (800, 600)] {
                    let view = PlayerChrome(presentation: .gallery(state), language: language)
                        .environment(\.colorScheme, scheme).frame(width: CGFloat(width), height: CGFloat(height))
                    let frame = NSRect(x: 0, y: 0, width: width, height: height)
                    let hosting = NSHostingView(rootView: view)
                    hosting.frame = frame
                    let window = NSWindow(
                        contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
                    window.isReleasedWhenClosed = false
                    window.contentView = hosting
                    hosting.layoutSubtreeIfNeeded()
                    defer { window.close() }
                    let bitmap = try #require(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
                    hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
                    #expect(!window.isVisible)
                    let scale = CGFloat(bitmap.pixelsWide) / CGFloat(width)
                    let center = try #require(bitmap.colorAt(x: bitmap.pixelsWide / 2, y: bitmap.pixelsHigh / 2))
                    #expect(
                        center.alphaComponent > 0.99 && center.redComponent < 0.01 && center.greenComponent < 0.01
                            && center.blueComponent < 0.01)
                    let footer = try #require(bitmap.colorAt(x: Int(5 * scale), y: Int(CGFloat(height - 5) * scale)))
                    #expect(footer.alphaComponent > 0.99 && footer != center)
                    if state == .paused {
                        let y =
                            CGFloat(height) - SyncWatchTokens.layoutPlayerFooterHeight + SyncWatchTokens
                            .controlPlayerSliderHeight / 2
                        let progress = try #require(
                            bitmap.colorAt(x: Int(110 * scale), y: Int(y * scale))?.usingColorSpace(.sRGB))
                        #expect(progress.blueComponent > progress.redComponent + 0.2)
                    }
                    if let output {
                        let data = try #require(bitmap.representation(using: .png, properties: [:]))
                        try data.write(
                            to: output.appendingPathComponent(
                                "player\(state.rawValue)-\(name)-\(language)-\(width)x\(height).png"))
                    }
                }
            }
        }
    }
}
