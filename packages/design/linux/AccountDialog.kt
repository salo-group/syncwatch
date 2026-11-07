package syncwatch.design

import androidx.compose.runtime.Composable

data class AccountPresentation(val service: String = "", val browserProof: String = "", val state: String = "signedOut", val name: String = "", val email: String = "", val status: String = "accountSignedOut", val storageNotice: String = "accountMemoryOnly", val storageBlocked: Boolean = false) {
    override fun toString() = "[account presentation redacted]"
    val context: DesignContext get() {
        val codeRequired = state == "browserConfirmationRequired"; val pending = state in setOf("pending", "authorizing"); val confirmation = state == "confirmAccount"; val signedIn = state == "signedIn"; val canStart = !storageBlocked && state in setOf("signedOut", "failed")
        val recovery = storageBlocked && state in setOf("signedOut", "failed")
        return DesignContext(mapOf("namespace" to "account", "service" to service, "browserProof" to browserProof, "name" to name, "email" to email, "status" to status,
            "storageNotice" to storageNotice, "storageRecovery" to recovery.toString(), "can.accountRetryStorage" to recovery.toString(), "can.accountForgetStorage" to recovery.toString(), "can.accountUseMemory" to recovery.toString(),
            "pending" to pending.toString(), "confirmation" to confirmation.toString(), "codeRequired" to codeRequired.toString(), "signedIn" to signedIn.toString(),
            "hasProfile" to (confirmation || signedIn).toString(), "showSignIn" to canStart.toString(), "canCancel" to (pending || codeRequired || confirmation).toString(),
            "can.editAccountService" to canStart.toString(), "can.accountSignIn" to (canStart && service.isNotEmpty()).toString(),
            "can.accountOpenBrowser" to (state == "pending").toString(), "can.accountConfirm" to confirmation.toString(), "can.accountVerifyBrowserProof" to (codeRequired && browserProof.isNotEmpty()).toString(), "can.editAccountBrowserProof" to codeRequired.toString(),
            "can.accountCancel" to (pending || codeRequired || confirmation).toString(), "can.accountSignOut" to signedIn.toString(), "can.close" to "true"))
    }
}
@Composable fun AccountDialog(presentation: AccountPresentation, language: String, theme: SyncWatchTheme, onAction: (String, String?) -> Unit) {
    DesignSurface("Account", presentation.context, language, theme) { onAction(it.action, it.value) }
}
