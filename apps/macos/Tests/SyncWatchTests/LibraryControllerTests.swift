import Foundation
import SyncWatchLibrary
import Testing

@testable import SyncWatch

@Test @MainActor func applicationLibraryOwnership() async throws {
    let files = FileManager.default
    let scratch = files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let root = scratch.appendingPathComponent("movies")
    try files.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? files.removeItem(at: scratch) }
    try Data("fixture".utf8).write(to: root.appendingPathComponent("A.MKV"))
    try Data("fixture".utf8).write(to: root.appendingPathComponent("B.mp4"))
    let empty = scratch.appendingPathComponent("empty")
    try files.createDirectory(at: empty, withIntermediateDirectories: false)
    let store = FileLibraryAccessStore(url: scratch.appendingPathComponent("state/library-access.json"))
    let model = LibraryController(store: store)
    model.restore()
    await model.settled()
    #expect(model.presentation.entries.isEmpty)
    #expect(model.selectedMedia() == nil)
    model.choose(root)
    #expect(model.presentation.state == .scanning)
    #expect(model.selectedMedia() == nil)
    await model.settled()
    #expect(model.errorKey == nil)
    #expect(model.presentation.entries.map(\.title) == ["A.MKV", "B.mp4"])
    let ids = model.presentation.entries.map(\.id)
    let last = try #require(ids.last)
    model.select(last)
    let owned = try #require(model.selectedMedia())
    weak var selectedAccess = owned.access
    #expect(owned.media.id == last && owned.media.title == "B.mp4")
    model.rescan()
    await model.settled()
    #expect(model.presentation.selectedID == last && model.presentation.entries.map(\.id) == ids)
    let restored = LibraryController(store: store, selectedID: last)
    restored.restore()
    await restored.settled()
    #expect(restored.presentation.entries.map(\.id) == ids && restored.presentation.selectedID == last)
    model.choose(root.appendingPathComponent("absent"))
    await model.settled()
    #expect(model.errorKey == "libraryUnavailable" && model.presentation.entries.map(\.id) == ids)
    model.choose(root)
    model.choose(root.appendingPathComponent("absent"))
    await model.settled()
    #expect(model.presentation.state != .scanning && model.presentation.entries.map(\.id) == ids)
    model.choose(root)
    model.choose(empty)
    await model.settled()
    #expect(model.errorKey == nil && model.presentation.entries.isEmpty)
    #expect(model.selectedMedia() == nil && selectedAccess != nil)
    #expect(URL(fileURLWithPath: model.folderPath).resolvingSymlinksInPath() == empty.resolvingSymlinksInPath())
    if case .loaded(let record) = try store.read() {
        #expect(URL(fileURLWithPath: record.rootPath).resolvingSymlinksInPath() == empty.resolvingSymlinksInPath())
    } else {
        Issue.record("Missing current folder record")
    }
    let future = Data("{\"version\":2,\"future\":true}".utf8)
    try future.write(to: store.url)
    model.choose(root)
    await model.settled()
    #expect(model.errorKey == "librarySaveFailed" && model.presentation.entries.count == 2)
    #expect(try Data(contentsOf: store.url) == future)
    model.choose(empty)
    model.close()
    await model.settled()
    #expect(model.selectedMedia() == nil && model.presentation.enabledActions.isEmpty)
    let retainedData = try Data(contentsOf: owned.media.file)
    #expect(owned.access === selectedAccess && retainedData == Data("fixture".utf8))
    #expect(try Data(contentsOf: store.url) == future)
    restored.close()
    selectedAccess = nil
}
