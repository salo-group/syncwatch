import SwiftUI

public enum PlayerVisualState: String, CaseIterable, Sendable {
    case loading, paused, playing, buffering, fullscreen, connectionInterrupted, ended
}

/// Observations and capabilities only. User input emits requests without assuming engine acknowledgement.
public struct PlayerPresentation: Sendable {
    public var state: PlayerVisualState = .loading
    public var title = ""
    public var roomNotice = ""
    public var positionMs: Int64 = 0
    public var durationMs: Int64 = 0
    public var volume = 1.0
    public var muted = false
    public var skipSeconds = 10
    public var hasPrevious = false
    public var hasNext = false
    public var enabledActions: Set<String> = []

    public init() {}
    public var isValid: Bool {
        (0...SyncWatchPlayer.maximumPositionMs).contains(durationMs)
            && (0...max(0, durationMs)).contains(positionMs)
            && volume.isFinite && (0...1).contains(volume) && (1...600).contains(skipSeconds)
    }
    public var playing: Bool { state == .playing || state == .fullscreen }
    public var displayedPosition: Double { min(Double(max(0, positionMs)), displayedDuration) }
    public var displayedDuration: Double { Double(min(max(0, durationMs), SyncWatchPlayer.maximumPositionMs)) }
    public func allows(_ action: String) -> Bool {
        guard enabledActions.contains(action), SyncWatchPlayer.actions.contains(action) else { return false }
        switch action {
        case "playPause", "seek", "skip", "previousFile", "nextFile", "selectAudio", "selectSubtitles":
            guard isValid,
                [.paused, .playing, .fullscreen].contains(state)
                    || (state == .ended && SyncWatchPlayer.completedNavigationActions.contains(action))
            else { return false }
            if action == "seek" || action == "skip" { return durationMs > 0 }
            if action == "previousFile" { return hasPrevious }
            if action == "nextFile" { return hasNext }
            return true
        case "setMovieVolume", "muteMovie": return isValid
        default: return true
        }
    }
    public func dispatch(_ action: String, value: Double? = nil, to handler: (String, Double?) -> Void) {
        guard allows(action) else { return }
        switch action {
        case "seek":
            guard let value, value.isFinite, value.rounded() == value, (0...Double(durationMs)).contains(value) else {
                return
            }
        case "skip":
            guard let value, value == -1 || value == 1, allows("seek") else { return }
            let target = min(Double(durationMs), max(0, Double(positionMs) + value * Double(skipSeconds) * 1000))
            if target != Double(positionMs) { handler("seek", target) }
            return
        case "setMovieVolume":
            guard let value, value.isFinite, (0...1).contains(value) else { return }
        default: guard value == nil else { return }
        }
        handler(action, value)
    }
    public static func time(_ milliseconds: Double) -> String {
        let seconds =
            Int(min(Double(SyncWatchPlayer.maximumPositionMs), max(0, milliseconds.isFinite ? milliseconds : 0))) / 1000
        return seconds >= 3600
            ? String(format: "%d:%02d:%02d", seconds / 3600, (seconds / 60) % 60, seconds % 60)
            : String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
    public static func gallery(_ state: PlayerVisualState) -> Self {
        var p = Self()
        p.state = state
        p.title = SyncWatchCopy.galleryMovies[1]
        p.durationMs = state == .loading ? 0 : 5_400_000
        p.positionMs = state == .ended ? p.durationMs : min(754_000, p.durationMs)
        p.volume = 0.7
        p.hasPrevious = true
        p.hasNext = true
        p.enabledActions = Set(SyncWatchPlayer.actions)
        return p
    }
}

public struct PlayerChrome: View {
    @Environment(\.designPresented) private var presented
    public var presentation: PlayerPresentation
    public var language: String
    public var videoSurface: AnyView?
    public var selectionMenu: SelectionMenuPresentation?
    public var onSelection: (SelectionMenuRequest) -> Void = { _ in }
    public var onDismissSelection: () -> Void = {}
    public var onAction: (String, Double?) -> Void
    public init(
        presentation: PlayerPresentation = PlayerPresentation(), language: String = "en", videoSurface: AnyView? = nil,
        selectionMenu: SelectionMenuPresentation? = nil,
        onAction: @escaping (String, Double?) -> Void = { _, _ in }
    ) {
        self.presentation = presentation
        self.language = language
        self.videoSurface = videoSurface
        self.selectionMenu = selectionMenu
        self.onAction = onAction
    }
    public func selectionHandlers(
        onSelection: @escaping (SelectionMenuRequest) -> Void, onDismiss: @escaping () -> Void
    ) -> Self {
        var copy = self
        copy.onSelection = onSelection
        copy.onDismissSelection = onDismiss
        return copy
    }
    public var body: some View {
        DesignSurface(
            "Player", context: DesignBindings.player(presentation), language: language,
            slots: ownedSlots
        ) { event in
            if event.action == "dismissSelection" {
                onDismissSelection()
            } else {
                presentation.dispatch(event.action, value: event.value.flatMap(Double.init), to: onAction)
            }
        }
        .nativeInputHost(
            NativePlayerInput(
                presentation: presentation, protectedFocus: !presented || selectionMenu != nil, onAction: onAction))
    }

    private var ownedSlots: [String: AnyView] {
        var slots = videoSurface.map { ["video": $0] } ?? [:]
        if let menu = selectionMenu {
            slots[menu.kind + "Selection"] = AnyView(
                SelectionMenu(presentation: menu, language: language, onSelection: onSelection))
        }
        return slots
    }
}
