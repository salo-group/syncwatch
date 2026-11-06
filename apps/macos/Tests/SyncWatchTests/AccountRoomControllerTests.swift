import Foundation
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_ACCOUNT_APP_FIXTURE"] != nil))
func accountRoomApprovalCancellationAndScopeReplacement() async throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_ROOM_ACCOUNT_APP_FIXTURE"])
    let fixture = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: URL(filePath: path))) as? [String: Any])
    let ownerRecord = try AccountSavedSession.decode(JSONSerialization.data(withJSONObject: fixture["owner"]!))
    let guestRecord = try AccountSavedSession.decode(JSONSerialization.data(withJSONObject: fixture["guest"]!))
    let ownerClient = AccountClient(address: try RoomAddress(ownerRecord.service))
    let guestClient = AccountClient(address: try RoomAddress(guestRecord.service))
    let ownerDevice = try await ownerClient.resume(ownerRecord)
    let guestDevice = try await guestClient.resume(guestRecord)
    let ownerAuthority = try ownerClient.roomAuthority(ownerDevice)
    let guestAuthority = try guestClient.roomAuthority(guestDevice)
    let ownerSource = RoomAccountTestSource(ownerAuthority)
    let guestSource = RoomAccountTestSource(guestAuthority)
    let host = RoomController(accountSource: { ownerSource.value })
    let peer = RoomController(accountSource: { guestSource.value })
    do {
        #expect(!host.editor.isValid && host.editor.requiresAccount)
        host.connect(); #expect(host.editor.state == "disconnected")
        host.bindAccount(ownerAuthority, name: ownerDevice.account.name)
        peer.bindAccount(guestAuthority, name: guestDevice.account.name)
        host.connect()
        try await roomSettles { host.connected }
        #expect(host.editor.owner && host.editor.accountName == ownerDevice.account.name)
        let superseded = try #require(await host.invitationForCopy())
        _ = try #require(await host.invitationForCopy())
        #expect(!host.canCopy(superseded))
        for outcome in ["cancel", "deny", "approve"] {
            let transfer = try #require(await host.invitationForCopy())
            #expect(host.canCopy(transfer))
            peer.editor.selectMode("joinRoom")
            peer.pasteInvitation(transfer.invitation.copyText())
            peer.connect()
            try await roomSettles { peer.editor.state == "awaitingApproval" && host.editor.pendingRequests.count == 1 && host.editor.pendingAvailable }
            #expect(host.editor.pendingRequests[0].name == guestDevice.account.name)
            #expect(host.editor.pendingRequests[0].email == guestDevice.account.email)
            if outcome == "cancel" {
                await peer.close(); await peer.close()
                try await roomSettles { host.editor.pendingRequests.isEmpty }
                #expect(host.connected)
            } else {
                host.decideAdmission(host.editor.pendingRequests[0].id, approve: outcome == "approve")
                if outcome == "deny" {
                    try await roomSettles { peer.editor.state == "failed" }
                    #expect(peer.editor.status == "roomAdmissionDenied")
                    await peer.close()
                } else {
                    try await roomSettles { peer.connected && host.editor.memberCount == 2 }
                    #expect(!peer.editor.owner)
                    let membership = try await peer.beginWatch().membership
                    #expect(peer.canContinuePlayback(membership))
                    if let token = fixture["browserFixtureToken"] as? String {
                        try await meetApplicationChecks(host: host, peer: peer, service: #require(URL(string: ownerRecord.service)),
                            fixtureToken: token, account: ownerDevice.account, setAccountAvailable: { available in
                                guestSource.value = available ? guestAuthority : nil; ownerSource.value = available ? ownerAuthority : nil
                            })
                    }
                    guestSource.value = nil
                    #expect(!peer.connected && !peer.canContinuePlayback(membership))
                    ownerSource.value = nil
                    #expect(!host.connected && !host.canCopy(transfer))
                    await peer.close(); await host.close()
                    host.invitationCopyCompleted(true, transfer: transfer)
                    #expect(host.editor.state == "disconnected" && host.editor.status.isEmpty)
                }
            }
        }
        try await guestClient.logout(guestDevice); try await ownerClient.logout(ownerDevice)
    } catch {
        await peer.close(); await host.close()
        await guestAuthority.close(); await ownerAuthority.close()
        await guestClient.close(); await ownerClient.close()
        throw error
    }
    await peer.close(); await host.close()
    await guestAuthority.close(); await ownerAuthority.close()
    await guestClient.close(); await ownerClient.close()
}

@MainActor private func roomSettles(_ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now + .seconds(20)
    while !condition() {
        guard ContinuousClock.now < deadline else { throw URLError(.timedOut) }
        try await Task.sleep(for: .milliseconds(20))
    }
}

@MainActor private final class RoomAccountTestSource {
    var value: RoomAccountAuthority?
    init(_ value: RoomAccountAuthority) { self.value = value }
}
