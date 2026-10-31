package syncwatch

import androidx.compose.runtime.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogWindow
import androidx.compose.ui.window.rememberDialogState
import syncwatch.design.*

/** The shared draft UI edits local preferences; playback and room owners keep their existing authority. */
@Composable internal fun ConfiguredPlaybackPane(controller: PlaybackController, preferences: AppearancePreferences,
    theme: SyncWatchTheme, onClose: () -> Unit, onFullscreen: () -> Unit, roomNotice: String = "",
    onSaved: () -> Unit = {}) {
    var open by remember(controller) { mutableStateOf(false) }
    var editor by remember(controller) { mutableStateOf(PlayerSettingsEditor()) }
    val language = preferences.state.preferences.language
    PlaybackPane(controller, language, theme, onClose, onFullscreen, roomNotice,
        skipSeconds = preferences.state.preferences.skipSeconds,
        onOpenSettings = { editor = preferences.playerSettingsEditor(); open = true })
    if (open) {
        DialogWindow(onCloseRequest = { open = false }, title = SyncWatchCopy.text("playerSettings", language),
            state = rememberDialogState(width = SyncWatchTokens.layoutSettingsWidth.dp, height = SyncWatchTokens.layoutPlayerSettingsHeight.dp)) {
            PlayerSettingsDialog(editor, { editor = it }, language = language, theme = theme,
                onSave = { values -> preferences.savePlayerSettings(values).also { if (it) onSaved() } },
                onClose = { open = false })
        }
    }
}
