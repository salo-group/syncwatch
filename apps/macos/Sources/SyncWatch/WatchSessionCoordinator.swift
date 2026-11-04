import Foundation
import SwiftUI
import SyncWatchDesign
import SyncWatchDomain
import SyncWatchPlayback

/// Executes local WatchSession resource effects; synchronization remains a separate authority.
@MainActor final class WatchSessionCoordinator: ObservableObject {
    @Published private(set) var player: PlaybackController?
    @Published private(set) var playerPresented = false
    @Published private(set) var runtimeAvailable = false
    @Published private(set) var phase: WatchPhase
    @Published var errorKey: String?
    var onEndWatching: (@MainActor () -> Void)?
    var onPlaylistUpdate: (@MainActor (WatchPlaylist) -> Void)?
    var onPlaylistSelection: (@MainActor (String) -> Void)?
    var mediaID: String? { session.mediaID }
    private var session: WatchSession
    private let preferences: AppearancePreferences
    private let media: MediaConfiguration
    private let runtime = VLCPlaybackRuntime()
    private var pendingLoad: Task<Void, Never>?
    private var pendingScope: PlaybackScope?
    private var releaseTask: Task<Void, Never>?
    private var releasingScope: PlaybackScope?
    private var checkpointTask: Task<Void, Never>?
    private var checkpointRequested = false
    private var hasWatchActivity = false
    private var closing = false
    private var shutdown: Task<Void, Never>?

    init(preferences: AppearancePreferences, media: MediaConfiguration) {
        self.preferences = preferences
        self.media = media
        // AppearancePreferences validates every snapshot before publishing it.
        session = try! WatchSession(localState: preferences.state)
        phase = session.phase
    }
    var scope: PlaybackScope { session.scope }
    var positionMs: Int64 { session.positionMs }

    func prepareRuntime() async {
        let available: Bool
        do {
            try await runtime.open(package: media.package)
            available = true
        } catch { available = false }
        guard !closing else { return }
        runtimeAvailable = available
        if !available { errorKey = "playbackFailed" }
    }
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        var result = source
        if closing {
            result.enabledActions = []
            return result
        }
        let liveSelection =
            source.selectedID == session.mediaID && player?.scope == session.scope
            && (phase == .active || phase == .preparing) && player?.failed == false
        if runtimeAvailable && !preferences.savingWatchState && !liveSelection {
            result.enabledActions.insert("startWatching")
        }
        if liveSelection {
            result.enabledActions.formUnion(["continueWatching", "endWatching"])
            result.enabledActions.subtract(["openLibraryAudio", "openLibrarySubtitles"])
        }
        if preferences.savingWatchState { result.enabledActions.remove("openSettings") }
        return liveSelection ? player!.libraryPresentation(result) : result
    }
    func start(
        _ selection: LibraryMediaSelection, requiresSharedAdmission: Bool = false, autoplay: Bool = false
    ) {
        guard runtimeAvailable, !closing else { return }
        do {
            for effect in try session.select(selection.media.id) {
                if case .release(let owner) = effect { release(owner) }
            }
            let effects = try session.start()
            hasWatchActivity = true
            errorKey = nil
            for effect in effects {
                switch effect {
                case .release(let owner): release(owner)
                case .load(let request):
                    load(
                        request, selection: selection, requiresSharedAdmission: requiresSharedAdmission,
                        autoplay: autoplay)
                }
            }
            publish()
            requestCheckpoint()
        } catch {
            errorKey = "playbackFailed"
            publish()
        }
    }
    func continueWatching() {
        guard !closing, player?.scope == session.scope, player?.failed == false else { return }
        do {
            try session.continueWatching()
            publish()
        } catch { errorKey = "playbackFailed" }
    }
    func showLibrary() {
        guard !closing else { return }
        session.returnToLibrary()
        publish()
        requestCheckpoint()
    }
    func end() {
        pendingLoad?.cancel()
        do {
            for effect in try session.end() {
                if case .release(let owner) = effect { release(owner) }
            }
        } catch {
            errorKey = "playbackFailed"
            closing = true
            release(session.scope)
        }
        publish()
        requestCheckpoint()
    }
    func preferencesChanged() {
        try? session.setPreferences(preferences.state.preferences)
        publish()
    }
    func close() async {
        if let shutdown {
            await shutdown.value
            return
        }
        shutdown = Task { [self] in
            closing = true
            end()
            let loading = pendingLoad
            await releaseTask?.value
            await loading?.value
            // A failed preparation can still own a surface before the domain loaded callback.
            await player?.close()
            player = nil
            await checkpointTask?.value
            await runtime.close()
        }
        await shutdown?.value
    }
    private func publish() {
        phase = session.phase
        playerPresented = !closing && session.playerPresented
    }
    private func load(
        _ request: WatchLoad, selection: LibraryMediaSelection, requiresSharedAdmission: Bool, autoplay: Bool
    ) {
        pendingLoad?.cancel()
        pendingScope = request.scope
        let prior = releaseTask
        pendingLoad = Task { [self] in
            defer {
                if pendingScope == request.scope {
                    pendingScope = nil
                    pendingLoad = nil
                }
            }
            await prior?.value
            guard !Task.isCancelled, !closing, session.scope == request.scope, session.phase == .preparing else {
                return
            }
            player = PlaybackController(
                launch: LocalPlaybackLaunch(file: selection.media.file, package: media.package, report: nil),
                runtime: runtime,
                scope: request.scope, positionMs: request.positionMs, selection: selection,
                requiresSharedAdmission: requiresSharedAdmission, autoplay: autoplay,
                onLoaded: { [weak self] in self?.loaded($0) },
                onObservation: { [weak self] in self?.observed($0) },
                onNavigation: { [weak self] action in
                    if action == "showLibrary" {
                        self?.showLibrary()
                    } else if action == "endWatching" {
                        if let handler = self?.onEndWatching { handler() } else { self?.end() }
                    }
                },
                onFailure: { [weak self] in self?.failed(request.scope) },
                onPlaylistUpdate: { [weak self] in self?.onPlaylistUpdate?($0) },
                onPlaylistSelection: { [weak self] in self?.onPlaylistSelection?($0) },
                initialVolume: preferences.openingMovieVolume,
                onVolumeChanged: { [weak self] in self?.volumeChanged($0, value: $1) })
        }
    }
    private func release(_ owner: PlaybackScope) {
        if pendingScope == owner { pendingLoad?.cancel() }
        guard let current = player, current.scope == owner, releasingScope != owner else { return }
        releasingScope = owner
        releaseTask = Task { [self, current] in
            await current.close()
            if player === current { player = nil }
            if releasingScope == owner { releasingScope = nil }
        }
    }
    private func loaded(_ owner: PlaybackScope) {
        guard !closing, owner == session.scope, phase == .preparing else { return }
        do {
            try session.loaded(owner)
            publish()
        } catch { failed(owner) }
    }
    private func volumeChanged(_ owner: PlaybackScope, value: Double) {
        guard !closing, owner == session.scope, phase == .active, preferences.rememberMovieVolume(value) else { return }
        preferencesChanged()
        requestCheckpoint()
    }
    private func observed(_ value: PlaybackObservation) {
        guard !closing, value.scope == session.scope, phase == .active, value.status == .ready else { return }
        do {
            try session.recordPlayback(
                value.scope, positionMs: value.positionMs, durationMs: value.durationMs, paused: value.paused,
                ended: value.ended)
        } catch { failed(value.scope) }
    }
    private func failed(_ owner: PlaybackScope) {
        guard !closing, owner == session.scope else { return }
        errorKey = "playbackFailed"
        end()
    }
    private func requestCheckpoint() {
        guard hasWatchActivity else { return }
        checkpointRequested = true
        guard checkpointTask == nil else { return }
        checkpointTask = Task { [self] in
            defer { checkpointTask = nil }
            while checkpointRequested {
                checkpointRequested = false
                if !(await preferences.persistWatchState(session.localState)), errorKey == nil {
                    errorKey = "saveFailed"
                }
            }
        }
    }
}
