import Foundation
import SwiftUI
import SyncWatchBridge
import SyncWatchDesign
import SyncWatchDomain
import SyncWatchLibrary
import SyncWatchRoom

enum SharedMediaAvailabilityFailure: Error { case unavailable }

/// Joins metadata admission, the loaded native input and the helper without transferring native ownership.
@MainActor final class RoomWatchCoordinator: ObservableObject {
    @Published private(set) var busy = false
    @Published private(set) var status = ""
    @Published var errorKey: String?
    private let room: RoomController
    private let watch: WatchSessionCoordinator
    private let helper: HelperConfiguration
    private let library: LibraryController?
    private struct NextWatch {
        let selection: LibraryMediaSelection
        let forceSelection: Bool
    }
    private var pendingSelection: LibraryMediaSelection?
    private var work: Task<Void, Never>?
    private var active: (membership: RoomWatchMembership, context: RoomWatchContext, player: PlaybackController)?
    private var seenMembership = false
    private var closing = false
    private var admitted = false
    @Published private var completed = false

    init(
        room: RoomController, watch: WatchSessionCoordinator, helper: HelperConfiguration,
        library: LibraryController? = nil
    ) {
        self.room = room
        self.watch = watch
        self.helper = helper
        self.library = library
    }
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        var result = room.libraryPresentation(source, sharedStartAvailable: !busy && active == nil)
        if room.connected, !busy, active == nil, watch.runtimeAvailable, source.selectedID != nil {
            result.enabledActions.insert("startWatching")
        }
        if busy || closing { result.enabledActions.remove("startWatching") }
        if !status.isEmpty { result.roomStatus = status }
        if !busy, !room.mediaNotice.isEmpty { result.roomStatus = room.mediaNotice }
        return result
    }
    var playerNotice: String {
        if busy { return "" }
        if !room.mediaNotice.isEmpty { return room.mediaNotice }
        return active?.player.sharedState?.connection == .reconnecting ? "reconnecting" : ""
    }
    func membershipChanged() {
        if !room.connected {
            seenMembership = false
            return
        }
        guard !seenMembership else { return }
        seenMembership = true
        // Joining metadata cannot leave a previously local player authorized to play.
        if work == nil, let player = watch.player {
            player.requireSharedAdmission()
            busy = true
            work = Task { [self] in
                await player.revokeShared()
                busy = false
                work = nil
            }
        }
    }
    func start(_ selection: LibraryMediaSelection) {
        guard !closing, work == nil else { return }
        if room.editor.state == "disconnected" {
            watch.start(selection, autoplay: true)
            return
        }
        guard room.connected, watch.runtimeAvailable else { return }
        busy = true
        errorKey = nil
        status = "sharedWatchPreparing"
        work = Task { [self] in await run(selection) }
    }
    private func run(_ initial: LibraryMediaSelection, forceSelection: Bool = false) async {
        defer {
            busy = false
            work = nil
        }
        var next: NextWatch? = NextWatch(selection: initial, forceSelection: forceSelection)
        while !closing, !Task.isCancelled, let current = next {
            next = await runOne(current.selection, forceSelection: current.forceSelection)
        }
    }
    private func runOne(_ selection: LibraryMediaSelection, forceSelection: Bool) async -> NextWatch? {
        var publication: RoomPreparedPublication?
        var membership: RoomWatchMembership?
        var owned: PlaybackController?
        var finished = false
        var follow = false
        var startedGeneration = false
        admitted = false
        completed = false
        var stage = "membership"
        do {
            let intent = try await room.beginWatch()
            membership = intent.membership
            stage = "runtime"
            let runtime = try await helper.verify()
            try Task.checkCancellation()
            // A fresh scope prevents a prepared hash or helper from being reused for another load.
            stage = "input"
            watch.end()
            watch.start(selection, requiresSharedAdmission: true)
            let scope = watch.scope
            let deadline = ContinuousClock.now + .seconds(20)
            while owned == nil {
                try Task.checkCancellation()
                guard room.isCurrent(intent.membership), watch.scope == scope, watch.errorKey == nil,
                    ContinuousClock.now < deadline
                else { throw HelperSessionFailure.unavailable }
                // Native readiness can precede restored-position and track acknowledgement.
                if watch.phase == .active, let player = watch.player, player.scope == scope, !player.failed,
                    let actual = try? await player.player.observe(scope), actual.status == .ready,
                    actual.seekable, (actual.durationMs ?? 0) > 0
                {
                    owned = player
                } else {
                    try await Task.sleep(for: .milliseconds(100))
                }
            }
            guard let player = owned else { throw HelperSessionFailure.unavailable }
            stage = "media"
            let local = try await player.prepareSharedMedia()
            stage = "publication"
            publication = try await room.publishPrepared(local, membership: intent.membership)
            stage = "generation"
            var context = try await room.watchContext(intent.membership)
            try Self.requireSameGeneration(intent.context, context)
            if forceSelection || context.bootstrap.snapshot.watchStatus != "active" {
                guard let publication else { throw HelperSessionFailure.unavailable }
                stage = "selection"
                context = try await room.performWatch(
                    .select(mediaID: publication.media.mediaID), membership: intent.membership, expected: context)
                guard context.definition.selection?.media == publication.media else {
                    throw HelperSessionFailure.malformed
                }
                stage = "start"
                context = try await room.performWatch(
                    .start(mediaID: publication.media.mediaID), membership: intent.membership, expected: context)
                startedGeneration = true
            }
            try await requireAvailable(local, membership: intent.membership, context: context)
            stage = "agreement"
            let current = try await room.watchContext(intent.membership)
            try Self.requireSameGeneration(context, current)
            try await requireAvailable(local, membership: intent.membership, context: current)
            guard watch.player === player, watch.scope == scope else { throw HelperSessionFailure.unavailable }
            active = (intent.membership, current, player)
            stage = "helper"
            let transport = try await HelperProcess.launch(executable: runtime.executable, runtime: runtime.package)
            do {
                try Task.checkCancellation()
                guard authorized() else { throw HelperSessionFailure.unavailable }
                let connection = current.bootstrap.connection
                let remote = current.definition.selection!.media
                let admissionProvider: (@MainActor () async throws -> SyncplayAdmission)?
                if room.accountAuthority == nil {
                    admissionProvider = nil
                } else {
                    admissionProvider = { [weak self] in
                        guard let self else { throw HelperSessionFailure.unavailable }
                        return try await self.transportAdmission(intent.membership, expected: current, local: local)
                    }
                }
                try await player.connectShared(
                    transport: transport,
                    endpoint: SyncplayEndpoint(
                        host: connection.host, port: connection.port, room: connection.room,
                        username: intent.membership.identity.syncplayUsername),
                    media: SharedMedia(
                        handle: "loaded-" + UUID().uuidString, name: remote.title,
                        durationMs: local.durationMs!, fileSizeBytes: UInt64(local.fileSizeBytes)),
                    revision: runtime.revision, requireDeclaredIdentity: true,
                    authority: { [weak self] in self?.authorized() == true },
                    admissionProvider: admissionProvider)
            } catch {
                await transport.close()
                throw error
            }
            let readyDeadline = ContinuousClock.now + .seconds(20)
            while true {
                try Task.checkCancellation()
                if pendingSelection != nil { break }
                if admitted, room.connected, let latest = room.watchBootstrap,
                    latest.snapshot.epoch == current.bootstrap.snapshot.epoch,
                    latest.snapshot.watchGeneration > current.bootstrap.snapshot.watchGeneration,
                    ["selected", "active"].contains(latest.snapshot.watchStatus)
                {
                    player.requireSharedAdmission()
                    follow = true
                    break
                }
                let actual = try await player.player.observe(scope)
                if completed || actual.ended {
                    guard admitted else {
                        finished = true
                        status = "ended"
                        break
                    }
                    guard authorized() else { throw HelperSessionFailure.unavailable }
                    if !completed {
                        busy = true
                        await player.revokeShared()
                        if let prepared = publication {
                            room.clearPrepared(prepared.lease, membership: intent.membership)
                            publication = nil
                        }
                        if room.editor.owner, room.isCurrent(intent.membership) {
                            _ = try? await room.performWatch(
                                .end(reason: .finished), membership: intent.membership, expected: current)
                        }
                        try Task.checkCancellation()
                        completed = true
                        busy = false
                        status = "ended"
                    }
                    // Keep only generation observation; the completed helper is already joined.
                    try await Task.sleep(for: .milliseconds(100))
                    continue
                }
                guard authorized(), !player.failed else { throw HelperSessionFailure.unavailable }
                guard player.sharedState?.connection != .failed, player.sharedState?.connection != .stopped else {
                    throw HelperSessionFailure.unavailable
                }
                if player.sharedState?.connection == .connected && player.sharedState?.playbackReady == true {
                    guard player.sharedState?.acceptedUsername == intent.membership.identity.syncplayUsername else {
                        throw HelperSessionFailure.malformed
                    }
                    if !admitted {
                        if current.bootstrap.missingMembers.contains(intent.membership.memberID) {
                            let recovered = try await room.performWatch(
                                .missing(mediaID: current.definition.selection!.media.mediaID, missing: false),
                                membership: intent.membership, expected: current)
                            try Self.requireSameGeneration(current, recovered)
                        }
                        try player.admitSharedPlayback(autoplay: startedGeneration)
                        admitted = true
                        stage = "playback"
                    }
                    if busy || status == "reconnecting" {
                        busy = false
                        status = "sharedWatchConnected"
                    }
                } else if player.sharedState?.connection == .reconnecting {
                    status = "reconnecting"
                } else if !admitted, ContinuousClock.now >= readyDeadline {
                    throw HelperSessionFailure.timeout
                }
                try await Task.sleep(for: .milliseconds(100))
            }
        } catch _ where closing || Task.isCancelled {
            // Native and helper adapters can report transport errors after cancellation.
        } catch is CancellationError {
        } catch RoomProtocolFailure.rejected("staleGeneration") {
            // A simultaneous selection lost the compare-and-swap; follow the winning generation.
            follow = true
        } catch {
            let key = Self.failureKey(error, identityRejected: owned?.sharedState?.identityRejected == true)
            // Fixed stage/key vocabulary only: never serialize errors, paths or room credentials.
            FileHandle.standardError.write(Data("syncwatch.shared-watch failure stage=\(stage) key=\(key)\n".utf8))
            errorKey = key
            status = key
        }
        let previous = active
        let completedAdmission = admitted
        active = nil
        admitted = false
        completed = false
        await owned?.revokeShared()
        if !completedAdmission, let owned, watch.player === owned { watch.end() }
        if finished, let previous, room.editor.owner, room.isCurrent(previous.membership) {
            // Completion releases only this helper; bookkeeping cannot control a peer's movie.
            _ = try? await room.performWatch(
                .end(reason: .finished), membership: previous.membership, expected: previous.context)
        }
        if let publication, let membership { room.clearPrepared(publication.lease, membership: membership) }
        guard !closing, !Task.isCancelled else { return nil }
        if let requested = pendingSelection {
            pendingSelection = nil
            busy = true
            status = "sharedWatchPreparing"
            return NextWatch(selection: requested, forceSelection: true)
        }
        if follow, let membership {
            do {
                busy = true
                status = "sharedWatchPreparing"
                let deadline = ContinuousClock.now + .seconds(20)
                while true {
                    try Task.checkCancellation()
                    let current = try await room.watchContext(membership)
                    if current.bootstrap.snapshot.watchStatus == "active",
                        let media = current.definition.selection?.media
                    {
                        guard let candidate = resolvePlaylist(media) else {
                            _ = try await room.performWatch(
                                .missing(mediaID: media.mediaID, missing: true), membership: membership,
                                expected: current)
                            throw SharedMediaAvailabilityFailure.unavailable
                        }
                        library?.select(candidate.media.id)
                        return NextWatch(selection: candidate, forceSelection: false)
                    }
                    guard current.bootstrap.snapshot.watchStatus == "selected", ContinuousClock.now < deadline else {
                        throw HelperSessionFailure.unavailable
                    }
                    try await Task.sleep(for: .milliseconds(100))
                }
            } catch _ where closing || Task.isCancelled {
                return nil
            } catch is CancellationError { return nil } catch {
                watch.end()
                errorKey = Self.failureKey(error, identityRejected: false)
                status = errorKey!
            }
        }
        return nil
    }
    private func authorized() -> Bool {
        guard !closing, pendingSelection == nil, let active, watch.player === active.player,
            watch.scope == active.player.scope
        else {
            return false
        }
        // Metadata generations authorize a new load; Syncplay owns an already admitted file's timing.
        if admitted { return room.canContinuePlayback(active.membership) }
        guard room.isCurrent(active.membership), let current = room.watchBootstrap else { return false }
        return current.snapshot.epoch == active.context.bootstrap.snapshot.epoch
            && current.snapshot.watchGeneration == active.context.bootstrap.snapshot.watchGeneration
            && current.snapshot.watchStatus == "active"
    }
    private func canAdmitTransport() -> Bool {
        guard authorized(), let active,
            room.isCurrent(active.membership) || (admitted && room.canRenewWatchAdmission(active.membership)),
            let latest = room.watchBootstrap
        else {
            return false
        }
        return latest.snapshot.epoch == active.context.bootstrap.snapshot.epoch
            && latest.snapshot.watchGeneration == active.context.bootstrap.snapshot.watchGeneration
            && latest.snapshot.watchStatus == "active"
            && latest.snapshot.mediaID == active.context.bootstrap.snapshot.mediaID
    }
    private func transportAdmission(
        _ membership: RoomWatchMembership, expected: RoomWatchContext, local: MediaDescriptor
    )
        async throws -> SyncplayAdmission
    {
        // The helper bounds retries. Only an established scope may outlive the event transport.
        while true {
            try Task.checkCancellation()
            guard canAdmitTransport() else { throw HelperSessionFailure.unavailable }
            do {
                let ticket = try await room.syncAdmission(membership, expected: expected) { fresh in
                    try Self.requireSameGeneration(expected, fresh)
                    try Self.requireMatch(local, fresh)
                }
                guard canAdmitTransport() else { throw HelperSessionFailure.unavailable }
                return try SyncplayAdmission(
                    credential: ticket.admissionCredential(), clientRunID: membership.identity.clientRunID)
            } catch {
                try Task.checkCancellation()
                guard admitted, authorized(), room.editor.state == "reconnecting",
                    RoomController.isMetadataInterruption(error)
                else {
                    throw error
                }
                try await Task.sleep(for: .milliseconds(100))
            }
        }
    }
    static func failureKey(_ error: Error, identityRejected: Bool) -> String {
        if identityRejected { return "sharedWatchIdentityRejected" }
        if case RoomProtocolFailure.rejected("staleGeneration") = error { return "sharedWatchRoomChanged" }
        if error is SharedMediaAvailabilityFailure { return "sharedWatchMediaMissing" }
        return "sharedWatchFailed"
    }
    static func requireSameGeneration(_ expected: RoomWatchContext, _ current: RoomWatchContext) throws {
        guard expected.bootstrap.snapshot.epoch == current.bootstrap.snapshot.epoch,
            expected.bootstrap.snapshot.watchGeneration == current.bootstrap.snapshot.watchGeneration
        else { throw RoomProtocolFailure.rejected("staleGeneration") }
    }
    static func requireMatch(_ local: MediaDescriptor, _ context: RoomWatchContext) throws {
        guard context.bootstrap.snapshot.watchStatus == "active", let selected = context.definition.selection else {
            throw HelperSessionFailure.unavailable
        }
        guard try MediaAgreement.resolve(selected.media, local: [local]).status == .matched else {
            throw SharedMediaAvailabilityFailure.unavailable
        }
    }
    private func requireAvailable(_ local: MediaDescriptor, membership: RoomWatchMembership, context: RoomWatchContext)
        async throws
    {
        do { try Self.requireMatch(local, context) } catch let error as SharedMediaAvailabilityFailure {
            if !context.bootstrap.missingMembers.contains(membership.memberID),
                let selected = context.definition.selection
            {
                _ = try await room.performWatch(
                    .missing(mediaID: selected.media.mediaID, missing: true),
                    membership: membership, expected: context)
            }
            throw error
        }
    }
    private struct PlaylistKey: Hashable {
        let title: String
        let size: Int64
    }
    private func key(_ file: LocalMediaFile) -> PlaylistKey {
        PlaylistKey(
            title: MediaDescriptor.normalizedTitleKey(MediaDescriptor.logicalTitle(file.title)), size: file.sizeBytes)
    }
    private var sharedPlaylistAvailable: Bool {
        guard admitted, room.connected, let active, watch.player === active.player,
            watch.scope == active.player.scope
        else { return false }
        if !completed { return active.player.sharedState?.connection == .connected }
        guard room.isCurrent(active.membership), let latest = room.watchBootstrap else { return false }
        return Self.completedPlaylistAvailable(active.context.bootstrap.snapshot, latest.snapshot)
    }
    static func completedPlaylistAvailable(_ expected: RoomSnapshot, _ latest: RoomSnapshot) -> Bool {
        guard expected.epoch == latest.epoch, expected.watchStatus == "active", let media = expected.mediaID,
            media == latest.mediaID
        else { return false }
        if latest.watchGeneration == expected.watchGeneration { return latest.watchStatus == "active" }
        // watch.ended advances the same movie exactly once; later loads are separate authority.
        return latest.watchStatus == "ended" && latest.watchGeneration > expected.watchGeneration
            && latest.watchGeneration - expected.watchGeneration == 1
    }
    func updatePlaylist(_ playlist: WatchPlaylist) {
        guard let library, !closing else {
            playlist.update(entries: [], selectedID: nil, enabled: false)
            return
        }
        let files = Dictionary(uniqueKeysWithValues: library.roomFiles.map { ($0.id, $0) })
        let local = room.editor.state == "disconnected"
        let enabled =
            watch.phase == .active && !busy
            && (local || sharedPlaylistAvailable)
        let view = room.catalogView
        let members = view?.bootstrap.snapshot.members.map(\.memberID) ?? []
        let keys = Dictionary(grouping: view?.items ?? [], by: \.memberID).mapValues { entries in
            Set(entries.map { PlaylistKey(title: $0.media.titleKey, size: $0.media.fileSizeBytes) })
        }
        let entries = library.presentation.entries.filter { files[$0.id] != nil }.map {
            SelectionMenuEntry(id: $0.id, label: $0.title)
        }
        let neighbors = Set(
            files.values.filter { file in
                !local && members.count > 1 && members.allSatisfy { keys[$0]?.contains(key(file)) == true }
            }.map(\.id))
        playlist.update(entries: entries, selectedID: watch.mediaID, enabled: enabled, neighborIDs: neighbors)
    }
    func selectPlaylist(_ id: String) {
        guard !closing, !busy, let library, let selection = library.playlistMedia(id), watch.mediaID != id else {
            return
        }
        let check = WatchPlaylist()
        updatePlaylist(check)
        check.open()
        guard check.menu?.allows(id) == true else { return }
        if room.editor.state == "disconnected" {
            library.select(id)
            watch.start(selection, autoplay: true)
        } else if sharedPlaylistAvailable {
            pendingSelection = selection
            busy = true
            status = "sharedWatchPreparing"
            active?.player.requireSharedAdmission()
        }
    }
    private func resolvePlaylist(_ media: MediaDescriptor) -> LibraryMediaSelection? {
        guard let library else { return nil }
        let candidates = library.roomFiles.filter {
            key($0) == PlaylistKey(title: media.titleKey, size: media.fileSizeBytes)
        }
        guard candidates.count == 1 else { return nil }
        return library.playlistMedia(candidates[0].id)
    }
    func end() async {
        let previous = active
        await stop()
        if let previous, room.editor.owner, room.isCurrent(previous.membership) {
            do {
                _ = try await room.performWatch(
                    .end(reason: .user), membership: previous.membership, expected: previous.context)
            } catch {
                // Local End succeeded; metadata bookkeeping is best effort.
            }
        }
        watch.end()
        status = ""
    }
    private var accountBinding = UUID()
    private var accountBindingWork: Task<Void, Never>?
    func bindAccount(_ authority: RoomAccountAuthority?, name: String) async {
        guard !closing else { return }
        let revision = UUID()
        accountBinding = revision
        let previous = accountBindingWork
        // View task cancellation must not interrupt revocation or race the next account.
        let job = Task { [self] in
            await previous?.value
            guard !closing, accountBinding == revision else { return }
            if room.accountAuthority !== authority && room.hasRoomScope { await leave() }
            guard !closing, accountBinding == revision else { return }
            room.bindAccount(authority, name: name)
        }
        accountBindingWork = job
        await job.value
        if accountBinding == revision { accountBindingWork = nil }
    }
    func leave() async {
        await stop()
        await room.close()
        await watch.player?.revokeShared(releaseAdmission: true)
        status = ""
        seenMembership = false
    }
    private func stop() async {
        pendingSelection = nil
        work?.cancel()
        await work?.value
    }
    func close() async {
        accountBinding = UUID()
        closing = true
        await accountBindingWork?.value
        accountBindingWork = nil
        await stop()
    }
}
