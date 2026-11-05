import AppKit
import SwiftUI
import SyncWatchDesign

/// Observe membership inside the presentation host as well as the owning workspace.
@MainActor struct RoomConnectionSheet: View {
    @ObservedObject var room: RoomController
    @ObservedObject var account: AccountController
    let shared: RoomWatchCoordinator
    var language: String
    var dismiss: () -> Void
    @State private var showAccount = false

    var body: some View {
        RoomDialog(editor: $room.editor, language: language, onAdmissionDecision: room.decideAdmission, onAction: action)
            .interactiveDismissDisabled(room.editor.busy)
            .sheet(isPresented: $showAccount, onDismiss: { Task { await account.dismiss() } }) {
                AccountConnectionSheet(account: account, language: language) { showAccount = false }
            }
    }
    private func action(_ action: String) {
        switch action {
        case "connectRoom": room.connect()
        case "openAccount": showAccount = true
        case "leaveRoom": Task { await shared.leave() }
        case "pasteInvitation": room.pasteInvitation(NSPasteboard.general.string(forType: .string) ?? "")
        case "copyInvitation":
            Task {
                if let transfer = await room.invitationForCopy(), room.canCopy(transfer) {
                    NSPasteboard.general.clearContents()
                    room.invitationCopyCompleted(
                        NSPasteboard.general.setString(transfer.invitation.copyText(), forType: .string), transfer: transfer)
                }
            }
        case "close":
            if room.editor.busy {
                Task {
                    await room.close()
                    dismiss()
                }
            } else {
                room.clearSecrets()
                dismiss()
            }
        default: break
        }
    }
}
