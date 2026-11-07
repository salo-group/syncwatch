import SwiftUI
import SyncWatchDesign

struct MeetConnectionSheet: View {
    @ObservedObject var meet: MeetController
    var language: String
    var onClose: () -> Void
    var onOpenRoom: () -> Void
    var body: some View {
        MeetDialog(presentation: meet.presentation, language: language) { action, value in
            if action == "close" || action == "openRoom" {
                Task {
                    await meet.dismiss()
                    if action == "openRoom" { onOpenRoom() } else { onClose() }
                }
            } else {
                meet.action(action, value: value)
            }
        }
    }
}
