import AppKit
import Foundation
import SyncWatchBridge
import SyncWatchDomain
import SyncWatchLibrary
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_RECOVERY_PROBE"] != nil))
func authenticatedPlaybackSurvivesShortNetworkOutage() async throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_RECOVERY_PROBE"])
    let fixture = try #require(
        JSONSerialization.jsonObject(with: Data(contentsOf: URL(filePath: path))) as? [String: Any])
    func location(_ name: String) throws -> URL { URL(filePath: try #require(fixture[name] as? String)) }
    let owner = try #require(fixture["owner"])
    let record = try AccountSavedSession.decode(JSONSerialization.data(withJSONObject: owner))
    let state = FileLocalStateStore(url: try location("state"))
    let mediaPackage = try location("media")
    let helperPackage = try location("helper")
    let folderURL = try location("folder")
    let dropURL = try location("drop")
    let restoredURL = try location("restored")
    var initial = LocalState()
    initial.preferences.movieVolume = 0
    try state.write(initial)
    let client = AccountClient(address: try RoomAddress(record.service))
    let device: AccountDeviceSession
    let authority: RoomAccountAuthority
    do {
        device = try await client.resume(record)
    } catch {
        await client.close()
        throw error
    }
    do {
        authority = try client.roomAuthority(device)
    } catch {
        try? await client.logout(device)
        await client.close()
        throw error
    }
    let room = RoomController(accountSource: { authority })
    let watch = WatchSessionCoordinator(
        preferences: AppearancePreferences(store: state), media: MediaConfiguration(package: mediaPackage))
    let coordinator = RoomWatchCoordinator(
        room: room, watch: watch, helper: HelperConfiguration(package: helperPackage))
    func close() async {
        await coordinator.close()
        await watch.close()
        await room.close()
        try? await client.logout(device)
        await authority.close()
        await client.close()
    }
    do {
        room.bindAccount(authority, name: device.account.name)
        room.connect()
        try await recoveryProbeWait { room.connected }
        print("NATIVE_RECOVERY metadata established")
        let folder = try LibraryFolderAccess.selected(folderURL)
        let files = try await FileSystemLibrary().scan(root: folder.url, rootID: folder.rootId, recursive: false)
        let media = try #require(files.first { $0.title == "SyncWatch Shared Watch.avi" })
        await watch.prepareRuntime()
        try #require(watch.runtimeAvailable)
        coordinator.start(LibraryMediaSelection(media: media, access: folder))
        try await recoveryProbeWait { watch.player != nil || coordinator.errorKey != nil }
        let native = try #require(watch.player)
        let surface = NSView(frame: NSRect(x: 0, y: 0, width: 320, height: 180))
        native.attach(surface)
        print("NATIVE_RECOVERY native surface attached")
        try await recoveryProbeWait {
            coordinator.errorKey != nil
                || (watch.player?.sharedState?.connection == .connected
                    && watch.player?.sharedState?.playbackReady == true && !coordinator.busy)
        }
        try #require(coordinator.errorKey == nil)
        let previous = try await room.beginWatch()
        print("NATIVE_RECOVERY playback established")
        try Data().write(to: dropURL)
        try await recoveryProbeWait {
            watch.player?.sharedState?.connection == .reconnecting || coordinator.errorKey != nil
        }
        print("NATIVE_RECOVERY playback interrupted")
        try await recoveryProbeWait { FileManager.default.fileExists(atPath: restoredURL.path) }
        let outage = try #require(
            JSONSerialization.jsonObject(with: Data(contentsOf: restoredURL)) as? [String: Any])
        let interruption = try #require(outage["actualOutageSeconds"] as? Double)
        try #require((5..<10).contains(interruption))
        print("NATIVE_RECOVERY forward restored")
        try await recoveryProbeWait {
            coordinator.errorKey != nil
                || (room.connected && watch.player?.sharedState?.connection == .connected
                    && watch.player?.sharedState?.playbackReady == true)
        }
        try #require(coordinator.errorKey == nil, "Established playback terminated after a recoverable outage")
        let current = try await room.beginWatch()
        try #require(current.membership.memberID == previous.membership.memberID)
        try #require(current.membership.epoch == previous.membership.epoch)
        try #require(current.membership.identity.clientRunID == previous.membership.identity.clientRunID)
        try #require(
            current.context.bootstrap.snapshot.watchGeneration == previous.context.bootstrap.snapshot.watchGeneration)
        let mediaID = try #require(current.context.definition.selection?.media.mediaID)
        _ = try await room.performWatch(
            .missing(mediaID: mediaID, missing: false), membership: previous.membership, expected: current.context)
        print("NATIVE_RECOVERY recovery passed")
    } catch {
        await close()
        throw error
    }
    await close()
}

@MainActor private func recoveryProbeWait(_ condition: () -> Bool) async throws {
    let until = ContinuousClock.now + .seconds(30)
    while !condition() {
        guard ContinuousClock.now < until else { throw URLError(.timedOut) }
        try await Task.sleep(for: .milliseconds(20))
    }
}
