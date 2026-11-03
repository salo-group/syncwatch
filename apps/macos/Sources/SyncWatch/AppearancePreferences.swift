import AppKit
import Foundation
import SwiftUI
import SyncWatchDesign
import SyncWatchDomain
import SyncWatchLibrary

@MainActor final class AppearancePreferences: ObservableObject {
    @Published private(set) var state = LocalState()
    @Published private(set) var savingWatchState = false
    private(set) var canWrite = false
    private let store: SerializedLocalStateStore

    static var defaultURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SyncWatch/local-state.json")
    }

    init(store: any LocalStateStore) {
        self.store = SerializedLocalStateStore(store)
        do {
            switch try self.store.read() {
            case .loaded(let value):
                try value.validate()
                state = value
                canWrite = true
            case .missing, .invalid: canWrite = true
            case .unsupportedVersion: break
            }
        } catch { canWrite = false }
    }

    var editor: SettingsEditor {
        SettingsEditor(
            values: ["theme": state.preferences.theme, "language": state.preferences.language],
            editableFields: canWrite && !savingWatchState ? ["theme", "language"] : [],
            canSave: canWrite && !savingWatchState)
    }

    func save(_ values: [String: String], libraryOptions: Bool = false) -> Bool {
        guard canWrite, !savingWatchState, let theme = values["theme"], let language = values["language"] else {
            return false
        }
        var next = state
        next.preferences.theme = theme
        next.preferences.language = language
        if libraryOptions {
            guard let scan = values["scanSubfolders"], let refresh = values["automaticRefresh"],
                ["true", "false"].contains(scan), ["true", "false"].contains(refresh)
            else { return false }
            next.preferences.scanSubfolders = scan == "true"
            next.preferences.automaticRefresh = refresh == "true"
        }
        do {
            try next.validate()
            try store.write(next)
            state = next
            return true
        } catch { return false }
    }

    /// The WatchSession owner serializes checkpoints. Current appearance always wins the merge.
    func persistWatchState(_ value: LocalState) async -> Bool {
        guard !savingWatchState else { return false }
        var next = value
        next.preferences = state.preferences
        do { try next.validate() } catch { return false }
        state = next
        guard canWrite else { return false }
        savingWatchState = true
        defer { savingWatchState = false }
        let snapshot = next
        let destination = store
        return await Task.detached {
            do {
                try destination.write(snapshot)
                return true
            } catch { return false }
        }.value
    }

    var colorScheme: ColorScheme? {
        switch state.preferences.theme {
        case "dark": .dark
        case "light": .light
        default: nil
        }
    }
}

@MainActor struct ApplicationWorkspace: View {
    @StateObject private var preferences: AppearancePreferences
    @StateObject private var library: LibraryController
    @StateObject private var room: RoomController
    @StateObject private var watch: WatchSessionCoordinator
    @StateObject private var shared: RoomWatchCoordinator
    private let lifecycle: ApplicationLifecycle
    init(lifecycle: ApplicationLifecycle) {
        self.lifecycle = lifecycle
        let stateURL: URL
        if let index = CommandLine.arguments.firstIndex(of: "--state-directory"),
            index + 1 < CommandLine.arguments.count
        {
            stateURL = URL(filePath: CommandLine.arguments[index + 1]).appendingPathComponent("local-state.json")
        } else {
            stateURL = AppearancePreferences.defaultURL
        }
        let preferences = AppearancePreferences(store: FileLocalStateStore(url: stateURL))
        _preferences = StateObject(wrappedValue: preferences)
        let room = RoomController()
        let watch = WatchSessionCoordinator(
            preferences: preferences, media: MediaConfiguration.read(CommandLine.arguments))
        let library = LibraryController(
            store: FileLibraryAccessStore(
                url: stateURL.deletingLastPathComponent().appendingPathComponent(
                    "library-access.json")),
            selectedID: preferences.state.selectedMediaID,
            metadata: LibraryMetadataReader(media: MediaConfiguration.read(CommandLine.arguments)))
        library.configureOptions(
            scanSubfolders: preferences.state.preferences.scanSubfolders,
            automaticRefresh: preferences.state.preferences.automaticRefresh)
        let shared = RoomWatchCoordinator(
            room: room, watch: watch, helper: HelperConfiguration.read(CommandLine.arguments), library: library)
        _room = StateObject(wrappedValue: room)
        _watch = StateObject(wrappedValue: watch)
        _shared = StateObject(wrappedValue: shared)
        watch.onEndWatching = { [weak shared] in Task { await shared?.end() } }
        watch.onPlaylistUpdate = { [weak shared] in shared?.updatePlaylist($0) }
        watch.onPlaylistSelection = { [weak shared] in shared?.selectPlaylist($0) }
        _library = StateObject(wrappedValue: library)
    }
    @State private var showRoom = false
    @State private var showSettings = false
    @State private var editor = SettingsEditor()

    var body: some View {
        ApplicationChrome(
            playerPresented: watch.playerPresented,
            library: AnyView(
                LibraryWorkspace(
                    presentation: shared.libraryPresentation(watch.libraryPresentation(library.presentation)),
                    language: preferences.state.preferences.language
                ) { action, value in
                    if action != "openLibraryAudio" && action != "openLibrarySubtitles" { library.dismissTracks() }
                    if action == "openLibraryAudio" || action == "openLibrarySubtitles" {
                        library.openTracks(
                            action == "openLibraryAudio" ? "audio" : "subtitles",
                            language: preferences.state.preferences.language)
                    } else if action == "chooseFolder" {
                        chooseFolder()
                    } else if action == "rescan" {
                        library.rescan()
                    } else if action == "selectMovie", let value {
                        library.select(value)
                    } else if action == "startWatching", let selection = library.selectedMedia() {
                        shared.start(selection)
                    } else if action == "continueWatching" {
                        watch.continueWatching()
                    } else if action == "endWatching" {
                        Task { await shared.end() }
                    } else if action == "openRoom" {
                        showRoom = true
                    } else if action == "openSettings" {
                        editor = library.settingsEditor(preferences.editor)
                        showSettings = true
                    }
                }.selectionHandlers(
                    menu: watch.playerPresented ? nil : library.tracks.menu,
                    onSelection: library.selectTrack, onDismiss: library.dismissTracks)), player: playerContent
        )
        .background(WindowCloseGate(close: close))
        .onReceive(library.$roomRevision) { revision in
            room.updateLibrary(revision: revision, files: library.roomFiles)
        }
        .onChange(of: watch.playerPresented) { if $0 { library.dismissTracks() } }
        .onChange(of: room.connected) { _ in shared.membershipChanged() }
        .task {
            lifecycle.prepareForTermination = {
                [
                    weak sessionOwner = watch, weak catalogOwner = library, weak roomOwner = room,
                    weak sharedOwner = shared
                ] in
                await sharedOwner?.close()
                await sessionOwner?.close()
                await roomOwner?.close()
                await catalogOwner?.closeAndWait()
            }
            library.restore()
            await watch.prepareRuntime()
            await WatchAcceptance.run(preferences: preferences, library: library, watch: watch)
        }
        .onDisappear {
            let sessionOwner = watch
            let catalogOwner = library
            let roomOwner = room
            let sharedOwner = shared
            Task {
                await sharedOwner.close()
                await sessionOwner.close()
                await roomOwner.close()
                await catalogOwner.closeAndWait()
            }
        }
        .alert(
            SyncWatchCopy.text("brand", language: preferences.state.preferences.language),
            isPresented: Binding(
                get: {
                    !WatchAcceptance.requested
                        && (library.errorKey != nil || watch.errorKey != nil || shared.errorKey != nil)
                },
                set: {
                    if !$0 {
                        library.errorKey = nil
                        watch.errorKey = nil
                        shared.errorKey = nil
                    }
                })
        ) {
            Button(SyncWatchCopy.text("close", language: preferences.state.preferences.language)) {
                library.errorKey = nil
                watch.errorKey = nil
                shared.errorKey = nil
            }
        } message: {
            Text(
                SyncWatchCopy.text(
                    library.errorKey ?? watch.errorKey ?? shared.errorKey ?? "libraryUnavailable",
                    language: preferences.state.preferences.language))
        }
        .sheet(isPresented: $showSettings) {
            SettingsDialog(
                editor: $editor, language: preferences.state.preferences.language,
                folderPath: library.folderPath, canChooseFolder: library.canConfigure, onChooseFolder: chooseFolder,
                onSave: { values in
                    let options = editor.editableFields.contains("scanSubfolders")
                    guard !options || library.canConfigure else { return false }
                    let saved = preferences.save(values, libraryOptions: options)
                    if saved {
                        library.configureOptions(
                            scanSubfolders: preferences.state.preferences.scanSubfolders,
                            automaticRefresh: preferences.state.preferences.automaticRefresh)
                        watch.preferencesChanged()
                    }
                    return saved
                }, onClose: { showSettings = false })
        }
        .sheet(isPresented: $showRoom, onDismiss: { room.clearSecrets() }) {
            RoomConnectionSheet(room: room, shared: shared, language: preferences.state.preferences.language) {
                showRoom = false
            }
        }
        .preferredColorScheme(preferences.colorScheme)
        .environment(\.locale, Locale(identifier: preferences.state.preferences.language))
    }
    private var playerContent: AnyView {
        if let controller = watch.player {
            return AnyView(
                PlaybackPane(
                    controller: controller, language: preferences.state.preferences.language,
                    roomNotice: shared.playerNotice
                )
                .id(controller.scope.mediaGeneration))
        }
        return AnyView(EmptyView())
    }
    private func close() async {
        let sessionOwner = watch
        let catalogOwner = library
        await shared.close()
        await sessionOwner.close()
        await room.close()
        await catalogOwner.closeAndWait()
    }
    private func chooseFolder() {
        guard library.canConfigure else { return }
        let picker = NSOpenPanel()
        picker.canChooseDirectories = true
        picker.canChooseFiles = false
        picker.allowsMultipleSelection = false
        picker.title = SyncWatchCopy.text("folder", language: preferences.state.preferences.language)
        picker.prompt = SyncWatchCopy.text("chooseFolder", language: preferences.state.preferences.language)
        let selected: (NSApplication.ModalResponse) -> Void = { response in
            if response == .OK, let url = picker.url { library.choose(url) }
        }
        if let window = NSApp.keyWindow {
            picker.beginSheetModal(for: window, completionHandler: selected)
        } else {
            picker.begin(completionHandler: selected)
        }
    }

}
