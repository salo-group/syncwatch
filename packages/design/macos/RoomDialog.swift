import SwiftUI

public struct RoomAdmissionRow: Sendable, Equatable {
    public let id: String
    public let name: String
    public let email: String
    public init(id: String, name: String, email: String) {
        self.id = id
        self.name = name
        self.email = email
    }
}

/// Memory-only connection draft. This model never persists credentials.
public struct RoomEditor: Sendable {
    public private(set) var values = SyncWatchRoomForm.defaults
    public private(set) var mode = "createRoom"
    public var state = "disconnected"
    public var status = ""
    public var roomID = ""
    public var memberCount = 0
    public var owner = false
    public let flow: String
    public var accountReady = false
    public var accountName = ""
    public var accountService = ""
    public var pendingRequests: [RoomAdmissionRow] = []
    public var pendingAvailable = true
    public var deciding = false
    public var pendingStatus = ""
    public var requiresAccount: Bool { SyncWatchRoomForm.signInFlows.contains(flow) }
    private var needsAccount: Bool { requiresAccount && !accountReady }
    public init(values: [String: String] = [:], flow: String = SyncWatchRoomForm.defaultFlow) {
        self.flow = flow
        for field in SyncWatchRoomForm.fields where field.kind != "secret" {
            if let value = values[field.id] { self.values[field.id] = value }
        }
    }
    public var busy: Bool { state == "connecting" || state == "reconnecting" || state == "awaitingApproval" }
    public var connected: Bool { state == "connected" }
    public var fields: [SyncWatchRoomFormField] {
        let enabled = Set(SyncWatchRoomForm.flows[flow] ?? [])
        return SyncWatchRoomForm.fields.filter {
            enabled.contains($0.id) && ($0.section == "connection" || $0.section == mode)
        }
    }
    public var isValid: Bool {
        guard SyncWatchRoomForm.flows[flow] != nil, !needsAccount else { return false }
        return fields.allSatisfy {
            guard let value = values[$0.id], !value.isEmpty, value.utf16.count <= $0.maxLength,
                !value.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
            else { return false }
            return true
        }
    }
    public mutating func edit(_ id: String, value: String) {
        guard !busy, !connected, !needsAccount, let field = fields.first(where: { $0.id == id }),
            value.utf16.count <= field.maxLength
        else { return }
        values[id] = value
        status = ""
    }
    public mutating func selectMode(_ mode: String) {
        guard !busy, !connected, !needsAccount, self.mode != mode, ["createRoom", "joinRoom"].contains(mode) else {
            return
        }
        clearSecrets()
        self.mode = mode
        status = ""
    }
    public mutating func clearSecrets() {
        for field in SyncWatchRoomForm.fields where field.kind == "secret" { values[field.id] = "" }
    }
    public var context: DesignContext {
        let rows = fields.map {
            DesignContext([
                "id": $0.id, "label": $0.label, "value": values[$0.id] ?? "", "secret": String($0.kind == "secret"),
                "editable": String(!busy),
            ])
        }
        return DesignContext(
            [
                "namespace": "room",
                "showForm": String(
                    !connected && state != "reconnecting" && state != "awaitingApproval" && !needsAccount),
                "needsAccount": String(needsAccount), "showAccountIdentity": String(requiresAccount && accountReady),
                "accountName": accountName, "accountService": accountService,
                "showPendingRequests": String(connected && owner && requiresAccount),
                "pendingStatus": pendingStatus,
                "connected": String(connected || state == "reconnecting"),
                "createMode": String(mode == "createRoom"), "joinMode": String(mode == "joinRoom"),
                "connectLabel": mode,
                "status": SyncWatchRoomForm.statusCopy[state] ?? status, "roomID": roomID,
                "memberCount": String(memberCount),
                "can.close": "true", "can.openAccount": String(needsAccount && !busy),
                "can.cancelRequest": String(state == "awaitingApproval"),
                "can.chooseRoomMode": String(!busy && !connected && !needsAccount),
                "can.connectRoom": String(!busy && !connected && isValid),
                "can.leaveRoom": String(connected || state == "failed" || state == "reconnecting"),
                "can.copyInvitation": String(connected && owner),
                "can.pasteInvitation": String(!busy && !connected && mode == "joinRoom" && !needsAccount),
            ],
            collections: [
                "fields": rows,
                "pendingRequests": pendingRequests.map {
                    DesignContext([
                        "requestID": $0.id, "name": $0.name, "email": $0.email,
                        "can.decide": String(connected && owner && pendingAvailable && !deciding),
                    ])
                },
            ])
    }
}
public struct RoomDialog: View {
    @Binding private var editor: RoomEditor
    public var language: String
    public var onAction: (String) -> Void
    public var onAdmissionDecision: (String, Bool) -> Void
    public init(
        editor: Binding<RoomEditor>, language: String = "en",
        onAdmissionDecision: @escaping (String, Bool) -> Void = { _, _ in },
        onAction: @escaping (String) -> Void = { _ in }
    ) {
        _editor = editor
        self.language = language
        self.onAction = onAction
        self.onAdmissionDecision = onAdmissionDecision
    }
    public var body: some View {
        DesignSurface("Room", context: editor.context, language: language) { event in
            switch event.action {
            case "editRoomField": if let id = event.payload, let value = event.value { editor.edit(id, value: value) }
            case "chooseRoomMode": if let mode = event.payload { editor.selectMode(mode) }
            case "approveRoomAdmission", "denyRoomAdmission":
                if let id = event.payload { onAdmissionDecision(id, event.action == "approveRoomAdmission") }
            default: onAction(event.action)
            }
        }
    }
}
