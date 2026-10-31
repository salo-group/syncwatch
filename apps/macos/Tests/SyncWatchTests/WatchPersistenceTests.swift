import Foundation
import SyncWatchDomain
import Testing

@testable import SyncWatch

// Only the first write blocks; the semaphores coordinate the worker with the main actor.
private final class GatedDiskStore: LocalStateStore, @unchecked Sendable {
    let entered = DispatchSemaphore(value: 0)
    let proceed = DispatchSemaphore(value: 0)
    private let disk: FileLocalStateStore
    private let lock = NSLock()
    private var first = true
    init(url: URL) { disk = FileLocalStateStore(url: url) }
    func waitForWriter() -> Bool { entered.wait(timeout: .now() + 15) == .success }
    func read() throws -> LocalStateRead { try disk.read() }
    func write(_ state: LocalState) throws {
        let block = lock.withLock {
            let value = first
            first = false
            return value
        }
        if block {
            entered.signal()
            guard proceed.wait(timeout: .now() + 15) == .success else { throw CocoaError(.fileWriteUnknown) }
        }
        try disk.write(state)
    }
}

@MainActor @Test func watchCheckpointSerializesAppearanceAndPreservesFutureFiles() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let url = directory.appendingPathComponent("local-state.json")
    let disk = FileLocalStateStore(url: url)
    let gate = GatedDiskStore(url: url)
    let preferences = AppearancePreferences(store: gate)
    var snapshot = LocalState()
    snapshot.selectedMediaID = "movie-1"
    snapshot.resumePositions = [ResumePosition(mediaID: "movie-1", positionMs: 1234)]
    let writing = Task { await preferences.persistWatchState(snapshot) }
    defer { gate.proceed.signal() }
    let entered = await Task.detached { gate.waitForWriter() }.value
    #expect(entered)
    #expect(preferences.savingWatchState && !preferences.editor.canSave)
    #expect(!preferences.save(["theme": "dark", "language": "ru"]))
    #expect(!preferences.playerSettingsEditor.canSave)
    #expect(!preferences.savePlayerSettings(["skipSeconds": "30"]))
    #expect(!(await preferences.persistWatchState(snapshot)))
    gate.proceed.signal()
    #expect(await writing.value)
    #expect(!preferences.savingWatchState)
    #expect(preferences.save(["theme": "dark", "language": "ru"]))
    // This stale session snapshot must not overwrite the newly saved appearance.
    #expect(await preferences.persistWatchState(snapshot))
    let restored = AppearancePreferences(store: disk)
    #expect(restored.state.preferences.theme == "dark" && restored.state.preferences.language == "ru")
    #expect(restored.state.selectedMediaID == snapshot.selectedMediaID)
    #expect(restored.state.resumePositions == snapshot.resumePositions)
    let future = Data("{\"version\":2,\"future\":true}".utf8)
    try future.write(to: url)
    #expect(!(await preferences.persistWatchState(snapshot)))
    #expect(try Data(contentsOf: url) == future)
    let fresh = AppearancePreferences(store: disk)
    #expect(!(await fresh.persistWatchState(snapshot)))
    #expect(!fresh.save(["theme": "light", "language": "en"]))
    #expect(try Data(contentsOf: url) == future)
}
