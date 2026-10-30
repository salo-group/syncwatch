import AppKit
import SwiftUI
import SyncWatchBridge
import SyncWatchDesign
import SyncWatchDomain
import SyncWatchPlayback

/// Explicit local development entry; room/watch capabilities remain with their coordinator.
struct LocalPlaybackLaunch {
    let file: URL
    let package: URL
    let report: URL?
    static func read(_ arguments: [String] = CommandLine.arguments) -> Self? {
        guard let index = arguments.firstIndex(of: "--local-playback"), index + 1 < arguments.count else { return nil }
        let package = MediaConfiguration.read(arguments).package
        let report = arguments.firstIndex(of: "--playback-report").flatMap { i in
            i + 1 < arguments.count ? URL(filePath: arguments[i + 1]) : nil
        }
        return Self(file: URL(filePath: arguments[index + 1]), package: package, report: report)
    }
}

@MainActor final class PlaybackController: ObservableObject {
    @Published private(set) var presentation = PlayerPresentation()
    @Published private(set) var failed = false
    @Published private(set) var sharedState: HelperSessionState?
    private var checkpoint: SharedPlaybackCheckpoint?
    private var sharedSession: HelperSession?
    private var requiresSharedAdmission: Bool
    private var sharedMediaPreparation = SharedMediaPreparation()
    private var sharedAuthority: (@MainActor () -> Bool)?
    let player = VLCPlayback()
    private(set) var scope: PlaybackScope
    private(set) lazy var tracks = LocalPlaybackTracks(
        player: player, scope: { [weak self] in self?.scope },
        picker: ExternalTrackPicker(window: { [weak self] in self?.window }))
    let playlist = WatchPlaylist()
    private let onPlaylistUpdate: (@MainActor (WatchPlaylist) -> Void)?
    private let onPlaylistSelection: (@MainActor (String) -> Void)?
    var language = "en"
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        tracks.libraryPresentation(source)
    }
    private let launch: LocalPlaybackLaunch
    private let runtime: VLCPlaybackRuntime?
    private var polling: Task<Void, Never>?
    private(set) var view: NSView?
    private(set) var window: NSWindow?
    private var closing = false
    private var closed = false
    private let allocateScope: Bool
    private let initialPositionMs: Int64
    private var sharedResumePositionMs: Int64 = 0
    private var autoplayPending: Bool
    private var selection: LibraryMediaSelection?
    private let onLoaded: @MainActor (PlaybackScope) -> Void
    private let onObservation: @MainActor (PlaybackObservation) -> Void
    private let onNavigation: (@MainActor (String) -> Void)?
    private let onFailure: (@MainActor () -> Void)?

    init(
        launch: LocalPlaybackLaunch, runtime: VLCPlaybackRuntime? = nil, scope: PlaybackScope? = nil,
        positionMs: Int64 = 0,
        selection: LibraryMediaSelection? = nil,
        requiresSharedAdmission: Bool = false, autoplay: Bool = false,
        onLoaded: @escaping @MainActor (PlaybackScope) -> Void = { _ in },
        onObservation: @escaping @MainActor (PlaybackObservation) -> Void = { _ in },
        onNavigation: (@MainActor (String) -> Void)? = nil, onFailure: (@MainActor () -> Void)? = nil,
        onPlaylistUpdate: (@MainActor (WatchPlaylist) -> Void)? = nil,
        onPlaylistSelection: (@MainActor (String) -> Void)? = nil
    ) {
        self.launch = launch
        self.runtime = runtime
        self.scope = scope ?? .fresh()
        allocateScope = scope == nil
        initialPositionMs = positionMs
        self.selection = selection
        self.requiresSharedAdmission = requiresSharedAdmission
        autoplayPending = autoplay && !requiresSharedAdmission
        self.onLoaded = onLoaded
        self.onObservation = onObservation
        self.onNavigation = onNavigation
        self.onFailure = onFailure
        self.onPlaylistUpdate = onPlaylistUpdate
        self.onPlaylistSelection = onPlaylistSelection
        presentation.title = launch.file.lastPathComponent
        presentation.enabledActions = navigationActions
    }
    private var navigationActions: Set<String> {
        onNavigation == nil ? ["endWatching", "toggleFullscreen"] : ["endWatching", "toggleFullscreen", "showLibrary"]
    }
    func prepareSharedMedia() async throws -> MediaDescriptor {
        guard !closing, let selection else { throw HelperSessionFailure.unavailable }
        let descriptor = try await sharedMediaPreparation.prepare(selection, player: player, scope: scope)
        guard !closing else { throw CancellationError() }
        sharedResumePositionMs = min(initialPositionMs, descriptor.durationMs!)
        return descriptor
    }
    func requireSharedAdmission() {
        requiresSharedAdmission = true
        gateSharedActions()
    }
    func admitSharedPlayback(autoplay: Bool = false) throws {
        guard sharedSession != nil, sharedState?.connection == .connected, sharedState?.playbackReady == true,
            sharedAuthority?() != false
        else { throw HelperSessionFailure.unavailable }
        // Syncplay can clear its readiness flag when this user pauses. That does
        // not revoke the initial admission or prevent the next Play intent.
        requiresSharedAdmission = false
        if autoplay { try sharedSession?.play(from: sharedResumePositionMs) }
    }
    func revokeShared(releaseAdmission: Bool = false) async {
        checkpoint?.clear()
        requireSharedAdmission()
        sharedAuthority = nil
        let session = sharedSession
        sharedSession = nil
        sharedState = nil
        let preparation = sharedMediaPreparation
        await preparation.close()
        await session?.close()
        try? await player.setPaused(true, scope: scope)
        guard !closing else { return }
        sharedMediaPreparation = SharedMediaPreparation()
        requiresSharedAdmission = !releaseAdmission
    }
    /// The room coordinator must admit the selected media before transferring
    /// this transport. Native playback ownership stays with this controller.
    func connectShared(
        transport: any HelperTransport, endpoint: SyncplayEndpoint, media: SharedMedia, revision: String,
        requireDeclaredIdentity: Bool = false, authority: (@MainActor () -> Bool)? = nil
    )
        async throws
    {
        do {
            guard !closing, view != nil, sharedSession == nil else { throw HelperSessionFailure.unavailable }
            try await sharedMediaPreparation.validate(player: player, scope: scope)
            guard !closing, sharedSession == nil, authority?() ?? true else { throw HelperSessionFailure.unavailable }
            sharedAuthority = authority
            let session = try HelperSession(
                transport: transport, player: player, nativeScope: scope,
                endpoint: endpoint, media: media, revision: revision,
                requireDeclaredIdentity: requiresSharedAdmission || requireDeclaredIdentity
            ) { [weak self] state in
                if state.connection == .reconnecting { self?.checkpoint?.interrupt() }
                self?.sharedState = state
                self?.gateSharedActions()
            }
            sharedSession = session
            sharedState = session.state
            gateSharedActions()
            try session.start()
        } catch {
            await transport.close()
            throw error
        }
    }
    private var sharedPlaybackAvailable: Bool {
        !closing && sharedSession != nil && sharedAuthority?() != false
            && sharedState?.connection == .connected && sharedState?.playbackReady == true
    }
    private func gateSharedActions() {
        if sharedAuthority?() == false { checkpoint?.clear() }
        presentation.enabledActions.subtract(["restoreSharedPosition", "dismissSharedPosition"])
        presentation.sharedPositionMs =
            sharedPlaybackAvailable
            ? checkpoint?.position(durationMs: presentation.durationMs) : nil
        if presentation.sharedPositionMs != nil {
            presentation.enabledActions.formUnion(["restoreSharedPosition", "dismissSharedPosition"])
        }
        if (requiresSharedAdmission || sharedSession != nil)
            && (sharedAuthority?() == false || sharedState?.connection != .connected
                || (requiresSharedAdmission && sharedState?.playbackReady != true))
        {
            presentation.enabledActions.subtract(["playPause", "seek", "skip"])
        }
    }
    func attach(_ view: NSView) {
        guard self.view == nil, !closing else { return }
        self.view = view
        window = view.window
        polling = Task { [self] in
            do {
                if allocateScope { scope = try scope.replacingMedia() }
                checkpoint = SharedPlaybackCheckpoint(scope: scope)
                let address = UInt(bitPattern: Unmanaged.passUnretained(view).toOpaque())
                if let runtime {
                    try await player.open(runtime: runtime, appKitSurfaceAddress: address)
                } else {
                    try await player.open(package: launch.package, appKitSurfaceAddress: address)
                }
                try Task.checkCancellation()
                try await player.load(launch.file, scope: scope)
                var admissionPending = true
                var resumePending = initialPositionMs > 0
                if let report = launch.report {
                    Task { await PlaybackAcceptance.run(controller: self, report: report) }
                }
                while !Task.isCancelled {
                    let observation = try await player.observe(scope)
                    guard !closing else { break }
                    if observation.status == .failed { throw PlaybackFailure.engine }
                    // Restore the local position before track acknowledgement and admission.
                    if resumePending, observation.status == .ready {
                        resumePending = false
                        if observation.seekable {
                            // A saved position seeds a request; only the next native observation is published.
                            try? await player.seek(
                                to: min(initialPositionMs, observation.durationMs ?? initialPositionMs), scope: scope)
                            continue
                        }
                    }
                    if admissionPending, observation.status == .ready {
                        try await tracks.applyInitial(selection?.tracks ?? LocalTrackPreferences())
                        try Task.checkCancellation()
                        guard !closing else { break }
                        admissionPending = false
                        onLoaded(scope)
                        continue
                    }
                    apply(observation)
                    onObservation(observation)
                    if autoplayPending, observation.status == .ready, !observation.ended {
                        autoplayPending = false
                        try await player.setPaused(false, scope: scope)
                    }
                    try await Task.sleep(for: .milliseconds(100))
                }
            } catch is CancellationError {
            } catch {
                if !closing {
                    if let report = launch.report {
                        await PlaybackAcceptance.fail(controller: self, report: report, error: error)
                        return
                    }
                    failed = true
                    tracks.dismiss()
                    presentation.enabledActions = ["endWatching"]
                    onFailure?()
                }
            }
        }
    }
    private func refreshPlaylist() {
        onPlaylistUpdate?(playlist)
        presentation = playlist.presentation(presentation)
    }
    func selectMenu(_ request: SelectionMenuRequest) {
        refreshPlaylist()
        if request.kind == "playlist" {
            if let id = playlist.selection(request) { onPlaylistSelection?(id) }
        } else {
            tracks.select(request)
        }
    }
    func dismissMenu() {
        playlist.dismiss()
        tracks.dismissMenu()
    }
    private func apply(_ value: PlaybackObservation) {
        checkpoint?.observe(value, available: sharedPlaybackAvailable)
        presentation.state =
            value.ended
            ? .ended
            : value.buffering ? .buffering : value.status != .ready ? .loading : value.paused ? .paused : .playing
        presentation.positionMs = value.positionMs
        presentation.durationMs = value.durationMs ?? 0
        presentation.volume = value.volume ?? presentation.volume
        presentation.muted = value.muted
        presentation.enabledActions = navigationActions.union(["playPause"])
        if value.volume != nil { presentation.enabledActions.formUnion(["setMovieVolume", "muteMovie"]) }
        if value.seekable { presentation.enabledActions.formUnion(["seek", "skip"]) }
        if value.status == .ready, !value.ended {
            presentation.enabledActions.formUnion(["selectAudio", "selectSubtitles"])
        } else {
            tracks.dismiss()
        }
        gateSharedActions()
        refreshPlaylist()
    }
    func action(_ action: String, value: Double?) {
        guard !closing else { return }
        gateSharedActions()
        refreshPlaylist()
        guard presentation.enabledActions.contains(action) else { return }
        if action == "restoreSharedPosition" || action == "dismissSharedPosition" {
            guard value == nil, presentation.allows(action) else { return }
            do {
                if action == "restoreSharedPosition" {
                    guard let sharedSession, let position = presentation.sharedPositionMs else { return }
                    try sharedSession.seek(position)
                }
                checkpoint?.clear()
                gateSharedActions()
            } catch { /* A rejected request retains the offer; observations remain authoritative. */  }
            return
        }
        if action == "openPlaylist" {
            tracks.dismiss()
            playlist.open()
            return
        }
        if action == "previousFile" || action == "nextFile" {
            if let id = playlist.neighbor(action == "previousFile" ? -1 : 1) { onPlaylistSelection?(id) }
            return
        }
        playlist.dismiss()
        if action == "selectAudio" || action == "selectSubtitles" {
            tracks.open(action == "selectAudio" ? "audio" : "subtitles", language: language)
            return
        }
        if action == "showLibrary" || action == "endWatching" || action == "toggleFullscreen" { tracks.dismiss() }
        if action == "endWatching" || action == "showLibrary", let onNavigation {
            onNavigation(action)
            return
        }
        if action == "endWatching" {
            let ownedWindow = window
            Task {
                await close()
                ownedWindow?.close()
            }
            return
        }
        if action == "toggleFullscreen" {
            if #unavailable(macOS 15) {
                window?.collectionBehavior.remove(.fullScreenNone)
                window?.collectionBehavior.insert(.fullScreenPrimary)
            }
            window?.toggleFullScreen(nil)
            return
        }
        if let sharedSession, action == "playPause" || action == "seek" {
            do {
                if action == "playPause" {
                    try sharedSession.togglePause()
                } else if let value, value.isFinite, value >= 0, value <= Double(WatchSession.maximumPositionMs) {
                    try sharedSession.seek(Int64(value))
                }
                checkpoint?.clear()
                gateSharedActions()
            } catch {
                // A rejected shared intent leaves the observation authoritative.
            }
            return
        }
        let paused = presentation.playing
        let volume = presentation.volume
        let muted = presentation.muted
        Task {
            do {
                switch action {
                case "playPause": try await player.setPaused(paused, scope: scope)
                case "seek":
                    if let value, value.isFinite, value >= 0, value <= Double(WatchSession.maximumPositionMs) {
                        try await player.seek(to: Int64(value), scope: scope)
                    }
                case "setMovieVolume": if let value { try await player.setVolume(value, muted: muted, scope: scope) }
                case "muteMovie": try await player.setVolume(volume, muted: !muted, scope: scope)
                default: break
                }
            } catch {
                // A rejected request does not acknowledge a different state.
            }
        }
    }
    func detach(_ surface: NSView) {
        let ownedWindow = surface.window ?? window
        Task {
            await close()
            withExtendedLifetime((surface, ownedWindow)) {}
        }
    }
    func close() async {
        guard !closed else { return }
        closing = true
        checkpoint?.clear()
        presentation.sharedPositionMs = nil
        playlist.close()
        presentation.enabledActions = []
        polling?.cancel()
        await tracks.close()
        await sharedMediaPreparation.close()
        await sharedSession?.close()
        sharedSession = nil
        await player.close()
        tracks.releaseExternalAccess()
        await polling?.value
        polling = nil
        view = nil
        window = nil
        withExtendedLifetime(selection) {}
        selection = nil
        closed = true
    }
}

struct PlaybackPane: View {
    @ObservedObject var controller: PlaybackController
    var language: String
    var roomNotice = ""
    var body: some View {
        PlaybackTrackPane(
            controller: controller, tracks: controller.tracks, playlist: controller.playlist, language: language,
            roomNotice: roomNotice)
    }
}

private struct PlaybackTrackPane: View {
    @ObservedObject var controller: PlaybackController
    @ObservedObject var tracks: LocalPlaybackTracks
    @ObservedObject var playlist: WatchPlaylist
    var language: String
    var roomNotice: String
    private var presentation: PlayerPresentation {
        var result = playlist.presentation(controller.presentation)
        result.roomNotice = roomNotice
        return result
    }
    var body: some View {
        PlayerChrome(
            presentation: presentation, language: language,
            videoSurface: AnyView(VideoSurface(onAttach: controller.attach, onDetach: controller.detach)),
            selectionMenu: playlist.menu ?? tracks.menu,
            onAction: { action, value in
                controller.language = language
                controller.action(action, value: value)
            }
        ).selectionHandlers(onSelection: controller.selectMenu, onDismiss: controller.dismissMenu)
    }
}

struct PlaybackWorkspace: View {
    @StateObject private var controller: PlaybackController
    @StateObject private var preferences = AppearancePreferences(
        store: FileLocalStateStore(url: AppearancePreferences.defaultURL))
    init(launch: LocalPlaybackLaunch) { _controller = StateObject(wrappedValue: PlaybackController(launch: launch)) }
    @ViewBuilder var body: some View {
        if #available(macOS 15, *) {
            content.windowFullScreenBehavior(.enabled)
        } else {
            content
        }
    }
    private var content: some View {
        PlaybackPane(controller: controller, language: preferences.state.preferences.language)
            .preferredColorScheme(preferences.colorScheme)
            .environment(\.locale, Locale(identifier: preferences.state.preferences.language))
            .alert(
                SyncWatchCopy.text("playbackFailed", language: preferences.state.preferences.language),
                isPresented: Binding(
                    get: { controller.failed }, set: { _ in }
                )
            ) {
                Button(SyncWatchCopy.text("endWatching", language: preferences.state.preferences.language)) {
                    controller.action("endWatching", value: nil)
                }
            }
    }
}
