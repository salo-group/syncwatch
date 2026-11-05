import Testing

@testable import SyncWatchDesign

@Test func roomDraftKeepsCredentialsEphemeralAndCapabilitiesTruthful() {
    var editor = RoomEditor(values: ["username": "Alice", "creationKey": "must not restore"], flow: "legacy")
    #expect(editor.values["creationKey"] == "")
    #expect(!editor.isValid && editor.context.values["can.connectRoom"] == "false")
    editor.edit("creationKey", value: "creation-secret")
    editor.selectMode("createRoom")
    #expect(editor.values["creationKey"] == "creation-secret")
    editor.selectMode("joinRoom")
    #expect(editor.context.values["can.pasteInvitation"] == "true")
    #expect(editor.values["creationKey"] == "" && editor.values["username"] == "Alice")
    editor.edit("invitation", value: "invitation-secret")
    editor.state = "connecting"
    #expect(editor.context.values["can.pasteInvitation"] == "false")
    editor.edit("username", value: "Bob")
    editor.selectMode("createRoom")
    #expect(editor.values["username"] == "Alice" && editor.mode == "joinRoom")
    editor.clearSecrets()
    #expect(editor.values["invitation"] == "")
    editor.state = "connected"
    editor.owner = false
    #expect(editor.context.values["can.copyInvitation"] == "false")
    editor.owner = true
    #expect(editor.context.values["can.copyInvitation"] == "true")
    editor.state = "reconnecting"
    #expect(editor.busy && !editor.connected)
    #expect(editor.context.values["status"] == "reconnecting")
    #expect(editor.context.values["showForm"] == "false" && editor.context.values["can.leaveRoom"] == "true")
    #expect(
        editor.context.values["can.copyInvitation"] == "false" && editor.context.values["can.connectRoom"] == "false")
    editor.edit("username", value: "Changed")
    #expect(editor.values["username"] == "Alice")

}

@Test func accountRoomFlowUsesSharedFieldsAndBoundApprovalActions() {
    var editor = RoomEditor()
    #expect(editor.requiresAccount && !editor.isValid)
    #expect(editor.context.values["needsAccount"] == "true" && editor.context.values["showForm"] == "false")
    #expect(editor.context.values["can.openAccount"] == "true")
    editor.accountReady = true
    editor.accountName = "Owner"
    editor.accountService = "https://service.example.invalid/"
    #expect(editor.isValid && editor.fields.isEmpty)
    editor.selectMode("joinRoom")
    #expect(editor.fields.map(\.id) == ["roomID", "invitation"])
    editor.edit("serviceAddress", value: "https://foreign.example.invalid/")
    #expect(editor.values["serviceAddress"] == "")
    editor.edit("roomID", value: "room-test")
    editor.edit("invitation", value: String(repeating: "A", count: 64))
    #expect(editor.isValid)
    editor.state = "awaitingApproval"
    #expect(editor.busy && editor.context.values["can.cancelRequest"] == "true")
    #expect(editor.context.values["status"] == "roomAdmissionPending" && editor.context.values["showForm"] == "false")
    editor.state = "connected"; editor.owner = true
    editor.pendingRequests = [RoomAdmissionRow(id: "approval-handle", name: "Guest", email: "guest@example.invalid")]
    #expect(editor.context.collections["pendingRequests"]?.first?.values["requestID"] == "approval-handle")
    #expect(editor.context.collections["pendingRequests"]?.first?.values["can.decide"] == "true")
    editor.pendingAvailable = false
    #expect(editor.context.collections["pendingRequests"]?.first?.values["can.decide"] == "false")
    editor.pendingAvailable = true; editor.deciding = true
    #expect(editor.context.collections["pendingRequests"]?.first?.values["can.decide"] == "false")
    #expect(!RoomEditor(flow: "unknown").isValid)
}
