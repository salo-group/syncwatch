import Foundation
import SyncWatchDesign
import SyncWatchDomain
import Testing

@testable import SyncWatch

private final class TestStore: LocalStateStore {
    var current: LocalStateRead = .missing
    var failWrite = false
    var writes = 0
    func read() throws -> LocalStateRead { current }
    func write(_ state: LocalState) throws {
        if failWrite { throw CocoaError(.fileWriteNoPermission) }
        writes += 1
        current = .loaded(state)
    }
}

@MainActor @Test func appearanceSavePreservesSessionPreferencesAndAcknowledgesActualWrites() throws {
    let store = TestStore()
    var initial = LocalState()
    initial.preferences.skipSeconds = 42
    initial.selectedMediaID = "movie-1"
    initial.resumePositions = [ResumePosition(mediaID: "movie-1", positionMs: 9000)]
    store.current = .loaded(initial)
    let preferences = AppearancePreferences(store: store)
    #expect(preferences.editor.editableFields == ["theme", "language"])
    #expect(preferences.save(["theme": "dark", "language": "ru", "folder": "/not-persisted"]))
    #expect(store.writes == 1)
    #expect(preferences.state.preferences.theme == "dark" && preferences.state.preferences.language == "ru")
    #expect(preferences.state.preferences.skipSeconds == 42)
    #expect(preferences.state.resumePositions == initial.resumePositions)
    #expect(preferences.state.selectedMediaID == "movie-1")
    store.failWrite = true
    #expect(!preferences.save(["theme": "light", "language": "en"]))
    #expect(preferences.state.preferences.theme == "dark")
    #expect(!preferences.save(["theme": "invalid", "language": "en"]))
    #expect(store.writes == 1)
    store.current = .unsupportedVersion
    let future = AppearancePreferences(store: store)
    #expect(!future.canWrite && !future.editor.canSave)
    #expect(!future.save(["theme": "light", "language": "en"]))
    #expect(store.writes == 1)
}

@MainActor @Test func appearancePersistsAndRestoresThroughTheRealStore() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = FileLocalStateStore(url: directory.appendingPathComponent("local-state.json"))
    let preferences = AppearancePreferences(store: store)
    #expect(preferences.save(["theme": "light", "language": "ru"]))
    let restored = AppearancePreferences(store: store)
    #expect(restored.state.preferences.theme == "light")
    #expect(restored.state.preferences.language == "ru")
}

@MainActor @Test func playbackSettingsSaveIsLocalTransactionalAndRestored() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let disk = FileLocalStateStore(url: directory.appendingPathComponent("local-state.json"))
    var initial = LocalState()
    initial.preferences.language = "ru"
    initial.preferences.movieVolume = 0.4
    initial.preferences.skipSeconds = 42
    initial.selectedMediaID = "retained-film"
    initial.resumePositions = [ResumePosition(mediaID: "retained-film", positionMs: 12_345)]
    try disk.write(initial)
    let preferences = AppearancePreferences(store: disk)
    var draft = preferences.playerSettingsEditor
    #expect(draft.editableFields == ["skipSeconds"])
    #expect(draft.values["skipSeconds"] == "42" && !draft.isValid)
    draft.edit("skipSeconds", value: "30")
    draft.cancel()
    #expect(preferences.state == initial && draft.values["skipSeconds"] == "42")
    draft.edit("skipSeconds", value: "30")
    let saved = draft.save(using: preferences.savePlayerSettings)
    #expect(saved)
    var expected = initial
    expected.preferences.skipSeconds = 30
    #expect(preferences.state == expected)
    #expect(AppearancePreferences(store: disk).state == expected)
    // An older watch checkpoint must retain the just-saved local setting.
    #expect(await preferences.persistWatchState(initial))
    #expect(AppearancePreferences(store: disk).state == expected)
    for value in ["", "0", "42", "600", "30.0", "+30", " 30", "30\n"] {
        #expect(!preferences.savePlayerSettings(["skipSeconds": value]))
    }
    #expect(AppearancePreferences(store: disk).state == expected)
    let future = Data("{\"version\":2,\"future\":true}".utf8)
    try future.write(to: directory.appendingPathComponent("local-state.json"))
    #expect(!preferences.savePlayerSettings(["skipSeconds": "15"]))
    let preserved = try Data(contentsOf: directory.appendingPathComponent("local-state.json"))
    #expect(preferences.state == expected && preserved == future)
    let readonly = AppearancePreferences(store: disk)
    #expect(!readonly.playerSettingsEditor.canSave)
    #expect(!readonly.savePlayerSettings(["skipSeconds": "15"]))
}

@MainActor @Test func playbackSettingsWriteFailureLeavesAppliedValueUnchanged() {
    let store = TestStore()
    let preferences = AppearancePreferences(store: store)
    store.failWrite = true
    var draft = preferences.playerSettingsEditor
    draft.edit("skipSeconds", value: "60")
    let saved = draft.save(using: preferences.savePlayerSettings)
    #expect(!saved)
    #expect(draft.message == "saveFailed" && preferences.state.preferences.skipSeconds == 10)
    #expect(store.writes == 0)
}
