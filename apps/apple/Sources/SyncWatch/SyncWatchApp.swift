import AppKit
import SwiftUI
import SyncWatchDomain

@main
struct SyncWatchApp: App {
    private let playbackScope = PlaybackScope.fresh()

    var body: some Scene {
        Window("SyncWatch", id: "main") {
            Text("SyncWatch")
                .font(.largeTitle)
                .frame(minWidth: 800, minHeight: 600)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .defaultSize(width: 1120, height: 760)
    }
}
