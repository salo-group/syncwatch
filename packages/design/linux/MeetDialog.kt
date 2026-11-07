package syncwatch.design

import androidx.compose.runtime.Composable

data class MeetPresentation(val authorizationState: String = "none", val browserProof: String = "", val name: String = "", val email: String = "",
    val status: String = "meetRoomRequired", val enabledActions: Set<String> = setOf("close", "openRoom")) {
    override fun toString(): String = "[Meet presentation redacted]"
    val context: DesignContext get() = DesignContext(mapOf("namespace" to "meet", "authorizationState" to authorizationState,
        "browserProof" to browserProof, "name" to name, "email" to email, "status" to status) + enabledActions.associate { "can.$it" to "true" })
}
@Composable fun MeetDialog(presentation: MeetPresentation, language: String, theme: SyncWatchTheme, onAction: (String, String?) -> Unit) {
    DesignSurface("Meet", presentation.context, language, theme) { onAction(it.action, it.value) }
}
