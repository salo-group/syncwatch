import AppKit
import Foundation
import SwiftUI
import SyncWatchDesign
import SyncWatchRoom

/// Room-owned permission and explicit browser handoff, independent of watch/player ownership.
@MainActor final class MeetController: ObservableObject {
    @Published private var model = MeetPresentation()
    private let room: RoomController
    private let openBrowser: @MainActor (URL) async -> Bool
    private var membership: RoomCallMembership?
    private var call: RoomCallStatus?
    private var permission: RoomMeetAuthorization?
    private var permissionStatus: RoomMeetAuthorizationStatus?
    private var operation: RoomCallOperation?
    private var endTarget: RoomCallStatus?
    private var work: Task<Void, Never>?
    private var cleanup: Task<Void, Never>?
    private var generation = UUID()
    private var visible = false, closing = false, busy = false, browserOpened = false
    private var notice: String?

    init(room: RoomController, openBrowser: @escaping @MainActor (URL) async -> Bool = { NSWorkspace.shared.open($0) }) {
        self.room = room; self.openBrowser = openBrowser
    }
    var presentation: MeetPresentation {
        if closing { var result = MeetPresentation(); result.enabledActions = []; return result }
        guard let membership, room.isCurrent(membership) else { return MeetPresentation() }
        return model
    }
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        var result = source
        if !closing { result.enabledActions.insert("openCall") }
        return result
    }
    func membershipChanged() {
        guard !closing else { return }
        let next = room.callMembership()
        if let next, let current = membership, next.owner === current.owner, next.memberID == current.memberID, next.epoch == current.epoch { return }
        if next == nil && membership == nil { return }
        invalidate(next)
        if visible, next != nil { refresh() }
    }
    func present() {
        guard !closing else { return }
        visible = true; membershipChanged()
        if membership != nil, work == nil { refresh() }
    }
    func dismiss() async {
        visible = false; invalidate(membership)
        await cleanup?.value
    }
    func close() async {
        closing = true; visible = false; invalidate(nil)
        await cleanup?.value
    }
    private func invalidate(_ next: RoomCallMembership?) {
        generation = UUID()
        let previous = work, priorCleanup = cleanup, flow = permission, old = membership
        previous?.cancel(); work = nil; membership = next
        call = nil; permission = nil; permissionStatus = nil; operation = nil; endTarget = nil
        notice = nil; busy = false; browserOpened = false; model = MeetPresentation()
        cleanup = Task {
            await priorCleanup?.value; await previous?.value
            if let flow, let old { _ = try? await old.owner.cancelMeetAuthorization(flow) }
        }
    }
    func action(_ action: String, value: String? = nil) {
        guard visible, presentation.enabledActions.contains(action), let current = membership, room.isCurrent(current) else { return }
        if action == "editMeetBrowserProof" {
            if let value, value.utf16.count <= 32 { model.browserProof = value; render() }
            return
        }
        if action == "meetEnd" { endTarget = call; render(); return }
        if action == "meetCancelEnd" { endTarget = nil; render(); return }
        launch { [self] member, token in
            switch action {
            case "meetAuthorize":
                let flow = try await member.owner.beginMeetAuthorization()
                guard valid(token, member) else { _ = try? await member.owner.cancelMeetAuthorization(flow); return }
                permission = flow; permissionStatus = nil; browserOpened = false; model.browserProof = ""
            case "meetOpenAuthorization":
                guard let flow = permission else { return }
                let accepted = await openBrowser(flow.browserURI)
                guard valid(token, member) else { return }
                browserOpened = accepted; if !accepted { notice = "meetBrowserUnavailable" }
            case "meetVerify", "meetConfirm":
                guard let flow = permission else { return }
                let code = model.browserProof.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
                let result = action == "meetVerify"
                    ? try await member.owner.verifyMeetAuthorization(flow, code: code)
                    : try await member.owner.confirmMeetAuthorization(flow, code: code)
                guard valid(token, member) else { return }; apply(result)
            case "meetCancelAuthorization":
                if let flow = permission { _ = try await member.owner.cancelMeetAuthorization(flow) }
                guard valid(token, member) else { return }
                permission = nil; permissionStatus = nil; browserOpened = false; model.browserProof = ""
            case "meetStart":
                let next = try await member.owner.callStartOperation(); guard valid(token, member) else { return }
                operation = next; let result = try await member.owner.requestCall(next)
                guard valid(token, member) else { return }; apply(result)
            case "meetConfirmEnd":
                guard let target = endTarget, call?.callID == target.callID, call?.generation == target.generation else { return }
                let next = try await member.owner.callEndOperation(target); guard valid(token, member) else { return }
                operation = next; endTarget = nil; let result = try await member.owner.requestCall(next)
                guard valid(token, member) else { return }; apply(result)
            case "meetOpen":
                guard let call, call.state == "ready", let raw = call.meetURI, let uri = URL(string: raw) else { return }
                let fresh = try await member.owner.callStatus()
                guard valid(token, member) else { return }
                apply(fresh)
                guard fresh.state == "ready", fresh.callID == call.callID, fresh.generation == call.generation, fresh.meetURI == raw else { return }
                let accepted = await openBrowser(uri)
                guard valid(token, member), self.call?.callID == call.callID, self.call?.generation == call.generation else { return }
                notice = accepted ? "meetHandedOff" : "meetBrowserUnavailable"
            case "meetRetry":
                // A lost mutation response is recovered by status polling, never by a new automatic POST.
                call = nil
            default: return
            }
        }
    }
    private func refresh() { launch { _, _ in } }
    private func launch(_ body: @escaping @MainActor (RoomCallMembership, UUID) async throws -> Void) {
        guard let member = membership, room.isCurrent(member), !closing else { return }
        let previous = work, priorCleanup = cleanup, token = UUID(); generation = token
        previous?.cancel(); busy = true; notice = nil; render()
        work = Task { [self] in
            await priorCleanup?.value; await previous?.value
            guard valid(token, member) else { return }
            do {
                try await body(member, token)
                guard valid(token, member) else { return }; busy = false; render()
                while valid(token, member) {
                    let delay: UInt64
                    if let flow = permission {
                        let result = try await member.owner.meetAuthorizationStatus(flow)
                        guard valid(token, member) else { return }; apply(result); delay = flow.pollAfterMs
                    } else {
                        let result = try await member.owner.callStatus()
                        guard valid(token, member) else { return }; apply(result); delay = result.pollAfterMs
                    }
                    render(); try await Task.sleep(for: .milliseconds(Int64(delay)))
                }
            } catch is CancellationError {} catch {
                if valid(token, member) {
                    busy = false
                    if let failure = error as? RoomProtocolFailure, case .malformed = failure, permission != nil { notice = "meetInvalidConfirmation" }
                    else { notice = "meetFailed" }
                    render()
                }
            }
        }
    }
    private func valid(_ token: UUID, _ member: RoomCallMembership) -> Bool {
        !Task.isCancelled && !closing && visible && generation == token && room.isCurrent(member)
    }
    private func apply(_ value: RoomMeetAuthorizationStatus) {
        permissionStatus = value
        if let reason = value.reason {
            notice = reason == "accountMismatch" ? "meetAccountMismatch" : reason == "invalidConfirmation" ? "meetInvalidConfirmation" : "meetPermissionUnavailable"
        }
        if ["complete", "cancelled", "failed", "expired"].contains(value.state) {
            permission = nil; permissionStatus = nil; model.browserProof = ""; browserOpened = false
            if value.state == "complete" { call = nil }
        }
    }
    private func apply(_ value: RoomCallStatus) {
        if call?.callID != value.callID || call?.generation != value.generation { endTarget = nil; notice = nil }
        call = value
    }
    private func render() {
        guard let member = membership, room.isCurrent(member), !closing else { model = MeetPresentation(); return }
        var next = MeetPresentation(); next.browserProof = model.browserProof; next.enabledActions = ["close"]
        next.authorizationState = permission == nil ? "none" : permissionStatus?.state ?? "pending"
        if browserOpened && next.authorizationState == "pending" { next.authorizationState = "authorizing" }
        if next.authorizationState == "confirmAuthorization", let account = permissionStatus?.account { next.name = account.name; next.email = account.email }
        if busy { next.status = "meetWorking" }
        else if let notice { next.status = notice }
        else if permission != nil {
            next.status = next.authorizationState == "browserConfirmationRequired" ? "meetBrowserCode"
                : next.authorizationState == "confirmAuthorization" ? "meetPermissionConfirmation" : "meetAuthorizing"
        } else if let call {
            next.status = ["idle": "meetIdle", "ready": "meetReady", "creating": "meetWorking", "ending": "meetWorking",
                "creationUnknown": "meetUnknown", "endUnknown": "meetUnknown", "ended": "meetEnded", "unavailable": "meetUnavailable"][call.state] ?? "meetFailed"
        } else { next.status = "meetWorking" }
        if !busy {
            if permission != nil {
                next.enabledActions.insert("meetCancelAuthorization")
                if next.authorizationState == "pending" { next.enabledActions.insert("meetOpenAuthorization") }
                if next.authorizationState == "browserConfirmationRequired" {
                    next.enabledActions.insert("editMeetBrowserProof")
                    if !next.browserProof.isEmpty { next.enabledActions.insert("meetVerify") }
                }
                if next.authorizationState == "confirmAuthorization" { next.enabledActions.insert("meetConfirm") }
            } else if let call {
                next.enabledActions.insert("meetRetry")
                if member.isOwner && call.authorizationRequired { next.enabledActions.insert("meetAuthorize") }
                if call.state == "ready" { next.enabledActions.insert("meetOpen") }
                if member.isOwner && !call.authorizationRequired {
                    if ["idle", "ended", "creationFailed"].contains(call.state) { next.enabledActions.insert("meetStart") }
                    if ["ready", "endFailed"].contains(call.state) { next.enabledActions.insert("meetEnd") }
                }
                if let target = endTarget, target.callID == call.callID, target.generation == call.generation {
                    next.status = "meetEndConfirmation"; next.enabledActions = ["close", "meetConfirmEnd", "meetCancelEnd"]
                }
            } else { next.enabledActions.insert("meetRetry") }
        }
        model = next
    }
}
