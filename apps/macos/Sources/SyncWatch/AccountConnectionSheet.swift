import SwiftUI
import SyncWatchDesign

@MainActor struct AccountConnectionSheet: View {
    @ObservedObject var account: AccountController
    var language: String
    var dismiss: () -> Void
    var body: some View {
        AccountDialog(presentation: account.presentation, language: language) { action, value in
            if action == "close" {
                Task {
                    await account.dismiss()
                    dismiss()
                }
            } else {
                account.action(action, value: value)
            }
        }
    }
}
