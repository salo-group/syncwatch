package syncwatch.design.tests

import syncwatch.design.RoomEditor
import syncwatch.design.RoomAdmissionRow

fun roomChecks() {
    var editor = RoomEditor(mapOf("username" to "Alice", "creationKey" to "must not restore"), flow = "legacy")
    check(editor.values["creationKey"] == "" && !editor.isValid && editor.context.values["can.connectRoom"] == "false")
    editor = editor.edit("creationKey", "creation-secret").selectMode("createRoom"); check(editor.values["creationKey"] == "creation-secret")
    editor = editor.selectMode("joinRoom"); check(editor.values["creationKey"] == "" && editor.values["username"] == "Alice")
    check(editor.context.values["can.pasteInvitation"] == "true")
    editor = editor.edit("invitation", "invitation-secret").copy(state = "connecting")
    check(editor.context.values["can.pasteInvitation"] == "false")
    editor = editor.edit("username", "Bob").selectMode("createRoom"); check(editor.values["username"] == "Alice" && editor.mode == "joinRoom")
    editor = editor.clearSecrets(); check(editor.values["invitation"] == "")
    editor = editor.copy(state = "connected", owner = false); check(editor.context.values["can.copyInvitation"] == "false")
    editor = editor.copy(owner = true); check(editor.context.values["can.copyInvitation"] == "true")
    editor = editor.copy(state = "reconnecting")
    check(editor.busy && !editor.connected && editor.context.values["status"] == "reconnecting")
    check(editor.context.values["showForm"] == "false" && editor.context.values["can.leaveRoom"] == "true")
    check(editor.context.values["can.copyInvitation"] == "false" && editor.context.values["can.connectRoom"] == "false")
    check(editor.edit("username", "Changed").values["username"] == "Alice")


    var account = RoomEditor()
    check(account.requiresAccount && !account.isValid && account.context.values["needsAccount"] == "true")
    check(account.context.values["showForm"] == "false" && account.context.values["can.openAccount"] == "true")
    account = account.copy(accountReady = true, accountName = "Owner", accountService = "https://service.example.invalid/")
    check(account.isValid && account.fields.isEmpty())
    account = account.selectMode("joinRoom"); check(account.fields.map { it.id } == listOf("roomID", "invitation"))
    account = account.edit("serviceAddress", "https://foreign.example.invalid/"); check(account.values["serviceAddress"] == "")
    account = account.edit("roomID", "room-test").edit("invitation", "A".repeat(64)); check(account.isValid)
    account = account.copy(state = "awaitingApproval")
    check(account.busy && account.context.values["can.cancelRequest"] == "true" && account.context.values["status"] == "roomAdmissionPending" && account.context.values["showForm"] == "false")
    account = account.copy(state = "connected", owner = true, pendingRequests = listOf(RoomAdmissionRow("approval-handle", "Guest", "guest@example.invalid")))
    check(account.context.collections["pendingRequests"]!!.single().values["requestID"] == "approval-handle")
    check(account.context.collections["pendingRequests"]!!.single().values["can.decide"] == "true")
    account = account.copy(pendingAvailable = false); check(account.context.collections["pendingRequests"]!!.single().values["can.decide"] == "false")
    account = account.copy(pendingAvailable = true, deciding = true); check(account.context.collections["pendingRequests"]!!.single().values["can.decide"] == "false")
    check(!RoomEditor(flow = "unknown").isValid)
}
