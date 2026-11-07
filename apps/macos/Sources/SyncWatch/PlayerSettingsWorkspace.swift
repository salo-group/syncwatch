import SwiftUI
import SyncWatchDesign

/// Hosts the shared draft UI; native playback and room authority remain with their existing owners.
struct ConfiguredPlaybackPane: View {
    @ObservedObject var controller: PlaybackController
    @ObservedObject var preferences: AppearancePreferences
    var roomNotice = ""
    var onSaved: () -> Void = {}
    var onOpenCall: (() -> Void)? = nil
    @State private var open = false
    @State private var editor = PlayerSettingsEditor()

    var body: some View {
        PlaybackPane(
            controller: controller, language: preferences.state.preferences.language, roomNotice: roomNotice,
            skipSeconds: preferences.state.preferences.skipSeconds,
            onOpenSettings: {
                editor = preferences.playerSettingsEditor
                open = true
            }, onOpenCall: onOpenCall
        ).sheet(isPresented: $open) {
            PlayerSettingsDialog(
                editor: $editor, language: preferences.state.preferences.language,
                onSave: { values in
                    let volume =
                        controller.presentation.enabledActions.contains("setMovieVolume")
                        ? controller.presentation.volume : nil
                    guard preferences.savePlayerSettings(values, currentMovieVolume: volume) else { return false }
                    onSaved()
                    return true
                }, onClose: { open = false })
        }
    }
}
