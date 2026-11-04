package syncwatch.design

import androidx.compose.runtime.Composable

data class AccountPresentation(val service: String = "", val state: String = "signedOut", val name: String = "", val email: String = "", val status: String = "accountSignedOut") {
    val context: DesignContext get() {
        val pending = state in setOf("pending", "authorizing"); val confirmation = state == "confirmAccount"; val signedIn = state == "signedIn"; val canStart = state in setOf("signedOut", "failed")
        return DesignContext(mapOf("namespace" to "account", "service" to service, "name" to name, "email" to email, "status" to status,
            "pending" to pending.toString(), "confirmation" to confirmation.toString(), "signedIn" to signedIn.toString(),
            "hasProfile" to (confirmation || signedIn).toString(), "showSignIn" to canStart.toString(), "canCancel" to (pending || confirmation).toString(),
            "can.editAccountService" to canStart.toString(), "can.accountSignIn" to (canStart && service.isNotEmpty()).toString(),
            "can.accountOpenBrowser" to (state == "pending").toString(), "can.accountConfirm" to confirmation.toString(),
            "can.accountCancel" to (pending || confirmation).toString(), "can.accountSignOut" to signedIn.toString(), "can.close" to "true"))
    }
}
@Composable fun AccountDialog(presentation: AccountPresentation, language: String, theme: SyncWatchTheme, onAction: (String, String?) -> Unit) {
    DesignSurface("Account", presentation.context, language, theme) { onAction(it.action, it.value) }
}
