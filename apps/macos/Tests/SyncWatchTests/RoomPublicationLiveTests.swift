import Foundation
import SyncWatchLibrary
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_PUBLICATION_ROOM"] != nil))
func normalRoomPublicationExchangesThreeNativeCatalogs() async throws {
    let environment = ProcessInfo.processInfo.environment
    let caseRoot = URL(filePath: try #require(environment["SYNCWATCH_PUBLICATION_CASE"]))
    let directory = caseRoot.appendingPathComponent("movies")
    guard !FileManager.default.fileExists(atPath: directory.path) else {
        throw NSError(domain: "owned-publication-directory", code: 1)
    }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let platform = "macOS"
    for suffix in ["A", "B"] {
        try Data("authored catalog fixture".utf8).write(
            to: directory.appendingPathComponent("\(platform) publication \(suffix).mkv"))
    }
    let rootID = UUID().uuidString.lowercased()
    let scanner = FileSystemLibrary()
    let controller = RoomController(legacyValidation: true)
    func wait(allowPublicationFailure: Bool = false, _ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(30)
        while !condition() {
            guard controller.editor.state != "failed",
                allowPublicationFailure || controller.editor.status != "roomCatalogPublishFailed"
            else {
                throw NSError(domain: "native-publication-failed", code: 1)
            }
            guard ContinuousClock.now < deadline else { throw NSError(domain: "publication-deadline", code: 1) }
            try await Task.sleep(for: .milliseconds(20))
        }
    }
    func waitFile(_ name: String) async throws {
        let deadline = ContinuousClock.now + .seconds(30)
        while !FileManager.default.fileExists(atPath: caseRoot.appendingPathComponent(name).path) {
            guard ContinuousClock.now < deadline else { throw NSError(domain: "publication-barrier", code: 1) }
            try await Task.sleep(for: .milliseconds(20))
        }
    }
    func signal(_ name: String) throws {
        try Data("{\"passed\":true}".utf8).write(to: caseRoot.appendingPathComponent(name), options: .atomic)
    }
    do {
        controller.updateLibrary(
            revision: UUID(), files: try await scanner.scan(root: directory, rootID: rootID, recursive: false))
        controller.editor.selectMode("joinRoom")
        for (field, value) in [
            "serviceAddress": try #require(environment["SYNCWATCH_ROOM_SERVICE"]), "username": "macOS normal publisher",
            "roomID": try #require(environment["SYNCWATCH_PUBLICATION_ROOM"]),
            "invitation": try #require(environment["SYNCWATCH_PUBLICATION_INVITATION"]),
        ] { controller.editor.edit(field, value: value) }
        controller.connect()
        let expected = Set(
            ["macOS", "Windows", "Linux"].flatMap { name in ["\(name) publication A", "\(name) publication B"] })
        try await wait { Set(controller.catalogView?.items.map { $0.media.title } ?? []) == expected }
        let original = try #require(controller.catalogView)
        #expect(original.items.count == 6 && Set(original.items.map { $0.media.mediaID }).count == 6)
        let ownID = try #require(original.items.first { $0.media.title == "macOS publication A" }?.media.mediaID)
        try signal("phase1.json")
        try await wait { FileManager.default.fileExists(atPath: caseRoot.appendingPathComponent("phase2").path) }
        try FileManager.default.removeItem(at: directory.appendingPathComponent("macOS publication B.mkv"))
        controller.updateLibrary(
            revision: UUID(), files: try await scanner.scan(root: directory, rootID: rootID, recursive: false))
        let finalTitles = Set(["macOS", "Windows", "Linux"].map { "\($0) publication A" })
        try await wait {
            Set(controller.catalogView?.items.map { $0.media.title } ?? []) == finalTitles
                && controller.catalogView?.items.count == 3
        }
        #expect(controller.catalogView?.items.first { $0.media.title == "macOS publication A" }?.media.mediaID == ownID)
        #expect(original.items.count == 6)
        try signal("phase2.json")
        if environment["SYNCWATCH_PUBLICATION_RECOVERY"] == "1" {
            try await waitFile("fail-network")
            controller.updateLibrary(
                revision: UUID(), files: try await scanner.scan(root: directory, rootID: rootID, recursive: false))
            try await wait { controller.editor.status == "roomCatalogPublishFailed" }
            #expect(controller.connected && controller.catalogView?.items.count == 3)
            try signal("failure.json")
            try await waitFile("retry-network")
            let changed = Data("authored catalog fixture updated after unknown outcome".utf8)
            try changed.write(to: directory.appendingPathComponent("macOS publication A.mkv"))
            controller.updateLibrary(
                revision: UUID(), files: try await scanner.scan(root: directory, rootID: rootID, recursive: false))
            try await wait(allowPublicationFailure: true) {
                controller.editor.status.isEmpty && controller.catalogView?.items.count == 3
                    && controller.catalogView?.items.allSatisfy { $0.media.fileSizeBytes == Int64(changed.count) }
                        == true
            }
            #expect(
                controller.connected
                    && controller.catalogView?.items.first { $0.media.title == "macOS publication A" }?.media.mediaID
                        == ownID
            )
            try signal("recovered.json")
        }

        try await wait { FileManager.default.fileExists(atPath: caseRoot.appendingPathComponent("finish").path) }
        await controller.close()
        await controller.close()
        #expect(controller.catalogView == nil && !controller.connected)
        try signal("closed.json")
    } catch {
        await controller.close()
        throw error
    }
}
