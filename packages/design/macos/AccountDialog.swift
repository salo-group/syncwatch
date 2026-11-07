import SwiftUI

/// Account observations only; the neutral Account tree owns composition and copy.
public struct AccountPresentation: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public var description: String { "[account presentation redacted]" }
    public var debugDescription: String { description }
    public var service = ""
    public var browserProof = ""
    public var state = "signedOut"
    public var name = ""
    public var email = ""
    public var status = "accountSignedOut"
    public var storageNotice = "accountMemoryOnly"
    public var storageBlocked = false
    public init() {}
    public var context: DesignContext {
        let pending = ["pending", "authorizing"].contains(state)
        let confirmation = state == "confirmAccount"
        let codeRequired = state == "browserConfirmationRequired"
        let signedIn = state == "signedIn"
        let recovery = storageBlocked && (state == "signedOut" || state == "failed")
        let canStart = !storageBlocked && (state == "signedOut" || state == "failed")
        return DesignContext([
            "namespace": "account", "service": service, "browserProof": browserProof, "name": name, "email": email,
            "status": status, "storageNotice": storageNotice, "storageRecovery": String(recovery),
            "can.accountRetryStorage": String(recovery), "can.accountForgetStorage": String(recovery),
            "can.accountUseMemory": String(recovery),
            "pending": String(pending), "confirmation": String(confirmation), "codeRequired": String(codeRequired),
            "signedIn": String(signedIn),
            "hasProfile": String(confirmation || signedIn), "showSignIn": String(canStart),
            "canCancel": String(pending || codeRequired || confirmation),
            "can.editAccountService": String(canStart), "can.accountSignIn": String(canStart && !service.isEmpty),
            "can.accountOpenBrowser": String(state == "pending"),
            "can.accountConfirm": String(confirmation),
            "can.accountVerifyBrowserProof": String(codeRequired && !browserProof.isEmpty),
            "can.editAccountBrowserProof": String(codeRequired),
            "can.accountCancel": String(pending || codeRequired || confirmation),
            "can.accountSignOut": String(signedIn),
            "can.close": "true",
        ])
    }
}
public struct AccountDialog: View {
    public var presentation: AccountPresentation
    public var language: String
    public var onAction: (String, String?) -> Void
    public init(
        presentation: AccountPresentation, language: String = "en", onAction: @escaping (String, String?) -> Void
    ) {
        self.presentation = presentation
        self.language = language
        self.onAction = onAction
    }
    public var body: some View {
        DesignSurface("Account", context: presentation.context, language: language) { onAction($0.action, $0.value) }
    }
}
