import SwiftUI

/// Account observations only; the neutral Account tree owns composition and copy.
public struct AccountPresentation: Sendable {
    public var service = ""
    public var state = "signedOut"
    public var name = ""
    public var email = ""
    public var status = "accountSignedOut"
    public init() {}
    public var context: DesignContext {
        let pending = ["pending", "authorizing"].contains(state)
        let confirmation = state == "confirmAccount"
        let signedIn = state == "signedIn"
        let canStart = state == "signedOut" || state == "failed"
        return DesignContext([
            "namespace": "account", "service": service, "name": name, "email": email, "status": status,
            "pending": String(pending), "confirmation": String(confirmation), "signedIn": String(signedIn),
            "hasProfile": String(confirmation || signedIn), "showSignIn": String(canStart),
            "canCancel": String(pending || confirmation),
            "can.editAccountService": String(canStart), "can.accountSignIn": String(canStart && !service.isEmpty),
            "can.accountOpenBrowser": String(state == "pending"), "can.accountConfirm": String(confirmation),
            "can.accountCancel": String(pending || confirmation), "can.accountSignOut": String(signedIn),
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
