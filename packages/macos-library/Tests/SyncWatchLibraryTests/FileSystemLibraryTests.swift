import Foundation
import Testing

@testable import SyncWatchLibrary

private struct Corpus: Decodable {
    struct File: Decodable {
        let path: String
        let content: String
    }
    struct Item: Decodable {
        let relativePath: String
        let id: String
        let sizeBytes: Int64
        let title: String
    }
    let rootId: String
    let otherRootId: String
    let files: [File]
    let expected: [Item]
}

@Test func catalogCorpus() async throws {
    let fixture = ProcessInfo.processInfo.environment["SYNCWATCH_LIBRARY_FIXTURE"]!
    let corpus = try JSONDecoder().decode(Corpus.self, from: Data(contentsOf: URL(fileURLWithPath: fixture)))
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    for file in corpus.files {
        let path = root.appendingPathComponent(file.path)
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(file.content.utf8).write(to: path)
    }
    // A matching symlink and a cycle must neither escape the root nor duplicate files.
    try FileManager.default.createSymbolicLink(
        at: root.appendingPathComponent("link.mp4"), withDestinationURL: root.appendingPathComponent("root.mkv"))
    try FileManager.default.createSymbolicLink(
        at: root.appendingPathComponent("nested/cycle"), withDestinationURL: root)
    let scanner = FileSystemLibrary()
    let aliasRoot = root.appendingPathComponent("root-alias")
    try FileManager.default.createSymbolicLink(at: aliasRoot, withDestinationURL: root)
    do {
        _ = try await scanner.scan(root: aliasRoot, rootID: corpus.rootId, recursive: true)
        Issue.record("Symbolic link accepted as the library root")
    } catch is CocoaError {}
    let result = try await scanner.scan(root: root, rootID: corpus.rootId, recursive: true)
    #expect(result.map(\.relativePath) == corpus.expected.map(\.relativePath))
    #expect(result.map(\.id) == corpus.expected.map(\.id))
    #expect(result.map(\.sizeBytes) == corpus.expected.map(\.sizeBytes))
    #expect(result.map(\.title) == corpus.expected.map(\.title))
    #expect(result.allSatisfy { $0.file.isFileURL && $0.modificationDate != nil })
    let flat = try await scanner.scan(root: root, rootID: corpus.rootId, recursive: false)
    #expect(flat.map(\.relativePath) == corpus.expected.filter { !$0.relativePath.contains("/") }.map(\.relativePath))
    let other = try await scanner.scan(root: root, rootID: corpus.otherRootId, recursive: true)
    #expect(Set(result.map(\.id)).isDisjoint(with: Set(other.map(\.id))))
    let cancelled = Task {
        withUnsafeCurrentTask { $0?.cancel() }
        return try await scanner.scan(root: root, rootID: corpus.rootId, recursive: true)
    }
    do {
        _ = try await cancelled.value
        Issue.record("Cancelled scan returned a snapshot")
    } catch is CancellationError {}
    do {
        _ = try await scanner.scan(root: root.appendingPathComponent("absent"), rootID: corpus.rootId, recursive: true)
        Issue.record("Missing root accepted")
    } catch { #expect(!(error is CancellationError)) }
    do {
        _ = try await scanner.scan(root: root, rootID: "invalid", recursive: true)
        Issue.record("Invalid root identity accepted")
    } catch { #expect(error is CocoaError) }
}
