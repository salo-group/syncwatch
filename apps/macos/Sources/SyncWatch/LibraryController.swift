import Foundation
import SwiftUI
import SyncWatchDesign
import SyncWatchLibrary

/// A local selection retains the OS folder grant independently of Library navigation or rescans.
/// Its path-based identifier is not evidence of a matching peer edition.
struct LibraryMediaSelection: Sendable {
    let media: LocalMediaFile
    let access: LibraryFolderAccess
    var tracks = LocalTrackPreferences()
}

private actor LibraryRecordIO {
    let store: any LibraryAccessStore
    init(_ store: any LibraryAccessStore) { self.store = store }
    func read() throws -> LibraryAccessRead {
        try Task.checkCancellation()
        return try store.read()
    }
    func write(_ record: LibraryAccessRecord) throws {
        try Task.checkCancellation()
        try store.write(record)
    }
}

/// Application state only; the common library owns rendering and action validation.
@MainActor final class LibraryController: ObservableObject {
    @Published private(set) var presentation = LibraryPresentation(enabledActions: ["chooseFolder", "openSettings"])
    @Published private(set) var tracks = LibraryTrackSelection()
    @Published var errorKey: String?
    @Published private(set) var roomRevision = UUID()
    var roomFiles: [LocalMediaFile] { catalog.values.sorted { $0.id < $1.id } }
    private let scanner = FileSystemLibrary(
        companionExtensions: Dictionary(
            uniqueKeysWithValues:
                SyncWatchSelectionMenu.externalFileFilters.flatMap { filter in
                    filter.extensions.map { ($0, filter.kind == "audio" ? LibraryCompanionKind.audio : .subtitles) }
                }))
    private var durations = LibraryDurationCache()
    private let persistence: LibraryRecordIO
    private var stable: LibraryPresentation?
    private var grant: LibraryFolderAccess?
    private var catalog: [String: LocalMediaFile] = [:]
    private var pending: Task<Void, Never>?
    private var monitor: LibraryChangeMonitor?
    private var monitorAccess: LibraryFolderAccess?
    private var monitorRecursive: Bool?
    private var configuredSubfolders: Bool?
    private var configuredRefresh: Bool?
    private var monitorID: UUID?
    private var monitorTasks: [UUID: Task<Void, Never>] = [:]
    private var generation = UUID()
    private var closed = false
    private let metadata: (any MediaMetadataReading)?
    private var metadataTask: Task<Void, Never>?
    private var metadataGeneration = UUID()
    private let savedSelection: String?

    init(store: any LibraryAccessStore, selectedID: String? = nil, metadata: (any MediaMetadataReading)? = nil) {
        persistence = LibraryRecordIO(store)
        savedSelection = selectedID
        self.metadata = metadata
    }
    func restore() {
        run { [persistence] in
            switch try await persistence.read() {
            case .loaded(let record): return try await Task.detached { try LibraryFolderAccess.restore(record) }.value
            case .missing, .invalid: return nil
            case .unsupportedVersion: throw LibraryAccessError.unsupportedVersion
            }
        }
    }
    func choose(_ url: URL) {
        let recursive = scanRecursively(grant)
        let refresh = refreshAutomatically(grant)
        run {
            try await Task.detached {
                try LibraryFolderAccess.selected(url, scanSubfolders: recursive, autoRefresh: refresh)
            }.value
        }
    }
    func rescan() { if let grant { run({ grant }, persistAccess: false) } }
    var folderPath: String { grant?.url.path ?? "" }
    var canConfigure: Bool { !closed && presentation.state != .scanning }
    func settingsEditor(_ base: SettingsEditor) -> SettingsEditor {
        SettingsEditor(
            values: base.values.merging([
                "scanSubfolders": scanRecursively(grant) ? "true" : "false",
                "automaticRefresh": refreshAutomatically(grant) ? "true" : "false",
            ]) { _, value in value },
            editableFields: base.editableFields.union(
                canConfigure && base.canSave ? ["scanSubfolders", "automaticRefresh"] : []),
            canSave: base.canSave)
    }
    @discardableResult func configureOptions(scanSubfolders: Bool?, automaticRefresh: Bool?) -> Bool {
        if configuredSubfolders == scanSubfolders && configuredRefresh == automaticRefresh { return !closed }
        guard canConfigure else { return false }
        configuredSubfolders = scanSubfolders
        configuredRefresh = automaticRefresh
        rescan()
        return true
    }
    private func scanRecursively(_ access: LibraryFolderAccess?) -> Bool {
        configuredSubfolders ?? access?.scanSubfolders ?? (SyncWatchSettings.defaults["scanSubfolders"] == "true")
    }
    private func refreshAutomatically(_ access: LibraryFolderAccess?) -> Bool {
        configuredRefresh ?? access?.autoRefresh ?? (SyncWatchSettings.defaults["automaticRefresh"] == "true")
    }
    private func sameRoomCatalog(_ files: [LocalMediaFile]) -> Bool {
        files.count == catalog.count
            && files.allSatisfy { file in
                guard let old = catalog[file.id] else { return false }
                return file.title == old.title && file.relativePath == old.relativePath
                    && file.sizeBytes == old.sizeBytes && file.modificationDate == old.modificationDate
            }
    }
    func select(_ id: String) {
        guard !closed else { return }
        presentation.dispatch("selectMovie", value: id) { _, _ in
            presentation.selectedID = id
            startMetadata()
        }
    }
    func selectedMedia() -> LibraryMediaSelection? {
        guard !closed, presentation.state == .selected, let id = presentation.selectedID,
            let media = catalog[id], let grant
        else { return nil }
        return LibraryMediaSelection(media: media, access: grant, tracks: tracks.preferences)
    }
    func playlistMedia(_ id: String) -> LibraryMediaSelection? {
        guard !closed, presentation.state != .scanning, let media = catalog[id], let grant else { return nil }
        return LibraryMediaSelection(media: media, access: grant)
    }
    func openTracks(_ kind: String, language: String) {
        guard !closed, presentation.allows(kind == "audio" ? "openLibraryAudio" : "openLibrarySubtitles") else {
            return
        }
        tracks.open(kind, language: language)
    }
    func selectTrack(_ request: SelectionMenuRequest) {
        guard !closed else { return }
        tracks.select(request)
        applyTrackSelection()
    }
    func dismissTracks() { tracks.dismiss() }
    private func applyTrackSelection() {
        presentation.metadataAudioName = tracks.audioName
        presentation.metadataSubtitleName = tracks.subtitleName
        presentation.metadataSubtitlesDisabled = tracks.subtitlesDisabled
        stable = presentation
    }
    func close() {
        closed = true
        tracks.reset()
        generation = UUID()
        pending?.cancel()
        metadataTask?.cancel()
        metadataGeneration = UUID()
        for task in monitorTasks.values { task.cancel() }
        monitor?.close()
        monitor = nil
        monitorAccess = nil
        grant = nil
        catalog = [:]
        durations.clear()
        presentation.enabledActions = []
    }
    func settled() async { await pending?.value }
    func metadataSettled() async { await metadataTask?.value }
    func closeAndWait() async {
        let monitors = Array(monitorTasks.values)
        close()
        await pending?.value
        await metadataTask?.value
        for task in monitors { await task.value }
        await metadata?.close()
    }

    private func cancelMetadata() {
        metadataGeneration = UUID()
        metadataTask?.cancel()
        tracks.reset()
        presentation.metadataAudioName = nil
        presentation.metadataSubtitleName = nil
        presentation.metadataSubtitlesDisabled = false
        presentation.metadataDurationMs = nil
        presentation.metadataWidth = 0
        presentation.metadataHeight = 0
        presentation.metadataAudioTracks = nil
        presentation.metadataSubtitleTracks = nil
        presentation.metadataLoading = false
        presentation.metadataUnavailable = false
    }
    private func publishDuration(_ value: Int64?, file: LocalMediaFile) {
        durations.record(value, for: file)
        presentation.entries = presentation.entries.map { row in
            row.id == file.id
                ? LibraryEntry(
                    id: row.id, title: row.title, location: row.location, durationMs: value,
                    folderMovie: row.folderMovie) : row
        }
    }
    private func startMetadata() {
        let previous = metadataTask
        cancelMetadata()
        stable = presentation
        guard let metadata, let selection = selectedMedia() else { return }
        let token = metadataGeneration
        // Selection has priority. One owner processes the remaining scan in display order.
        let files =
            [selection.media]
            + presentation.entries.compactMap { catalog[$0.id] }
            .filter { $0.id != selection.media.id }
        presentation.metadataLoading = true
        stable = presentation
        metadataTask = Task {
            defer { withExtendedLifetime(selection.access) {} }
            await previous?.value
            for file in files {
                guard !closed, metadataGeneration == token, !Task.isCancelled else { return }
                let selected = file.id == selection.media.id
                if !selected, durations.duration(for: file) != nil { continue }
                do {
                    let value = try await metadata.read(file)
                    try Task.checkCancellation()
                    guard !closed, metadataGeneration == token else { return }
                    publishDuration(value.durationMs, file: file)
                    if selected {
                        presentation.metadataDurationMs = value.durationMs
                        presentation.metadataWidth = value.width
                        presentation.metadataHeight = value.height
                        presentation.metadataAudioTracks =
                            value.audioTracks + file.companions.filter { $0.kind == .audio }.count
                        presentation.metadataSubtitleTracks =
                            value.subtitleTracks + file.companions.filter { $0.kind == .subtitles }.count
                        presentation.metadataLoading = false
                        tracks.reset(value, companions: file.companions)
                        presentation.enabledActions.formUnion(["openLibraryAudio", "openLibrarySubtitles"])
                        applyTrackSelection()
                    }
                    stable = presentation
                } catch is CancellationError { return } catch {
                    guard !closed, metadataGeneration == token else { return }
                    publishDuration(nil, file: file)
                    if selected {
                        presentation.metadataLoading = false
                        presentation.metadataUnavailable = true
                    }
                    stable = presentation
                }
            }
        }
    }

    private func replaceMonitor(_ source: LibraryChangeMonitor?, access: LibraryFolderAccess, recursive: Bool) {
        if let monitorID { monitorTasks[monitorID]?.cancel() }
        monitor?.close()
        monitor = source
        monitorAccess = source == nil ? nil : access
        monitorRecursive = source == nil ? nil : recursive
        guard let source else {
            monitorID = nil
            return
        }
        let id = UUID()
        monitorID = id
        monitorTasks[id] = Task { [weak self] in
            defer {
                source.close()
                self?.monitorTasks.removeValue(forKey: id)
                withExtendedLifetime(access) {}
            }
            do {
                for await _ in source.changes {
                    try await Task.sleep(for: .milliseconds(300))
                    await self?.pending?.value
                    try Task.checkCancellation()
                    guard let self, !closed, monitor === source else { return }
                    run({ access }, automatic: true, persistAccess: false)
                    await pending?.value
                    if !source.isActive {
                        // Reconciliation owns scan/watch failures, including errors already dismissed by the UI.
                        return
                    }
                }
            } catch is CancellationError {} catch {
                if let self, !closed, monitor === source { errorKey = "libraryWatchFailed" }
            }
        }
    }

    private func run(
        _ acquire: @escaping @Sendable () async throws -> LibraryFolderAccess?, automatic: Bool = false,
        persistAccess: Bool = true
    ) {
        guard !closed else { return }
        pending?.cancel()
        if !automatic { cancelMetadata() }
        let token = UUID()
        generation = token
        let previous = stable ?? presentation
        stable = previous
        if !automatic {
            errorKey = nil
            presentation.state = .scanning
        }
        pending = Task {
            var candidate: LibraryChangeMonitor?
            defer { candidate?.close() }
            do {
                guard let next = try await acquire() else {
                    if generation == token {
                        presentation = previous
                        startMetadata()
                    }
                    return
                }
                try Task.checkCancellation()
                let recursive = scanRecursively(next)
                let refresh = refreshAutomatically(next)
                let reuse =
                    refresh && monitor?.isActive == true && monitorAccess?.rootId == next.rootId
                    && monitorAccess?.url == next.url && monitorRecursive == recursive
                var watchFailed = false
                if refresh && !reuse {
                    do {
                        candidate = try await Task.detached {
                            try LibraryChangeMonitor(root: next.url, recursive: recursive)
                        }.value
                    } catch { watchFailed = true }
                }
                try Task.checkCancellation()
                let files = try await scanner.scan(
                    root: next.url, rootID: next.rootId, recursive: recursive)
                try Task.checkCancellation()
                guard generation == token, !closed else { return }
                if automatic, Dictionary(uniqueKeysWithValues: files.map { ($0.id, $0) }) == catalog {
                    if !reuse {
                        replaceMonitor(candidate, access: next, recursive: recursive)
                        candidate = nil
                    }
                    if watchFailed { errorKey = "libraryWatchFailed" }
                    return
                }
                if automatic { cancelMetadata() }
                // Local companion and folder-presentation changes must not publish room activity.
                let publishRoom = !automatic || !sameRoomCatalog(files)
                let ordered = files.sorted { a, b in
                    let left = a.title.lowercased()
                    let right = b.title.lowercased()
                    return left == right
                        ? a.relativePath.utf8.lexicographicallyPrecedes(b.relativePath.utf8)
                        : left.utf8.lexicographicallyPrecedes(right.utf8)
                }
                durations.reconcile(files)
                let rows = ordered.map {
                    LibraryEntry(
                        id: $0.id, title: $0.title, location: $0.file.path, durationMs: durations.duration(for: $0),
                        folderMovie: $0.folderMovie)
                }
                let selected =
                    [automatic ? presentation.selectedID : previous.selectedID, savedSelection].compactMap { $0 }.first
                { id in rows.contains { $0.id == id }
                } ?? rows.first?.id
                grant = next
                catalog = Dictionary(uniqueKeysWithValues: files.map { ($0.id, $0) })
                if publishRoom { roomRevision = UUID() }
                presentation = LibraryPresentation(
                    state: rows.isEmpty ? .empty : .selected, entries: rows, selectedID: selected,
                    enabledActions: ["chooseFolder", "rescan", "search", "selectMovie", "openSettings"])
                startMetadata()
                if !reuse {
                    replaceMonitor(candidate, access: next, recursive: recursive)
                    candidate = nil
                }
                if watchFailed { errorKey = "libraryWatchFailed" }
                guard persistAccess else { return }
                if let record = next.record {
                    do { try await persistence.write(record) } catch is CancellationError {} catch {
                        if generation == token { errorKey = "librarySaveFailed" }
                    }
                } else {
                    errorKey = "librarySaveFailed"
                }
            } catch is CancellationError {} catch {
                if generation == token, !closed {
                    if !automatic {
                        presentation = previous
                        startMetadata()
                    }
                    errorKey = "libraryUnavailable"
                }
            }
        }
    }
}
