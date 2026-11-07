import SwiftUI

/// Observations and action capabilities only; the neutral Meet tree owns composition and copy.
public struct MeetPresentation: Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public var description: String { "[Meet presentation redacted]" }
    public var debugDescription: String { description }
    public var authorizationState = "none"
    public var browserProof = ""
    public var name = ""
    public var email = ""
    public var status = "meetRoomRequired"
    public var enabledActions: Set<String> = ["close", "openRoom"]
    public init() {}
    public var context: DesignContext {
        var values = [
            "namespace": "meet", "authorizationState": authorizationState, "browserProof": browserProof,
            "name": name, "email": email, "status": status,
        ]
        for action in enabledActions { values["can." + action] = "true" }
        return DesignContext(values)
    }
}
public struct MeetDialog: View {
    public var presentation: MeetPresentation
    public var language: String
    public var onAction: (String, String?) -> Void
    public init(presentation: MeetPresentation, language: String = "en", onAction: @escaping (String, String?) -> Void)
    {
        self.presentation = presentation
        self.language = language
        self.onAction = onAction
    }
    public var body: some View {
        DesignSurface("Meet", context: presentation.context, language: language) { onAction($0.action, $0.value) }
    }
}
