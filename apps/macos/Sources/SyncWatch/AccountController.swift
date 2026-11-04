import AppKit
import SwiftUI
import SyncWatchDesign
import SyncWatchRoom

/// One window owns account work and memory-only credentials independently of playback.
@MainActor final class AccountController: ObservableObject {
    @Published var presentation = AccountPresentation()
    private var client: AccountClient?
    private var attempt: AccountSignIn?
    private var device: AccountDeviceSession?
    private var work: Task<Void, Never>?
    private var refreshWork: Task<Void, Never>?
    private var generation = UUID()
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        var result = source
        result.enabledActions.insert("openAccount")
        return result
    }
    func action(_ action: String, value: String? = nil) {
        switch action {
        case "editAccountService":
            if ["signedOut", "failed"].contains(presentation.state), let value, value.utf16.count <= 256 {
                presentation.service = value
            }
        case "editAccountBrowserProof":
            if presentation.state == "browserConfirmationRequired", let value, value.utf16.count <= 32 {
                presentation.browserProof = value
            }
        case "accountSignIn": start()
        case "accountOpenBrowser":
            guard presentation.state == "pending", let attempt else { return }
            if NSWorkspace.shared.open(attempt.browserURI) {
                presentation.state = "authorizing"
            } else {
                presentation.status = "accountBrowserUnavailable"
            }
        case "accountVerifyBrowserProof": verifyBrowser()
        case "accountConfirm": confirm()
        case "accountCancel":
            generation = UUID()
            presentation.state = "working"
            presentation.status = "accountWorking"
            let previous = work
            previous?.cancel()
            work = Task {
                await previous?.value
                await cancel()
            }
        case "accountSignOut": signOut()
        default: break
        }
    }
    private func start() {
        guard ["signedOut", "failed"].contains(presentation.state), attempt == nil else { return }
        let address: RoomAddress
        do { address = try RoomAddress(presentation.service) } catch {
            fail()
            return
        }
        let client = AccountClient(address: address)
        self.client = client
        let scope = UUID()
        generation = scope
        presentation.state = "working"
        presentation.status = "accountWorking"
        work = Task { [weak self] in
            guard let self else { return }
            do {
                let signIn = try await client.start()
                guard generation == scope else {
                    try? await client.cancel(signIn)
                    return
                }
                attempt = signIn
                presentation.state = "pending"
                presentation.status = "accountPending"
                while !Task.isCancelled {
                    try await Task.sleep(for: .seconds(2))
                    let status = try await client.poll(signIn)
                    guard generation == scope else { return }
                    if status.state == "browserConfirmationRequired" {
                        presentation.state = "browserConfirmationRequired"
                        presentation.status = "accountBrowserProofRequired"
                        return
                    }
                    if status.state == "confirmAccount", let account = status.account {
                        presentation.state = "confirmAccount"
                        presentation.status = "accountConfirmAccount"
                        presentation.name = account.name
                        presentation.email = account.email
                        return
                    }
                    if status.state == "authorizing" { presentation.state = "authorizing" }
                    if ["failed", "cancelled", "complete"].contains(status.state) {
                        await cancel()
                        fail()
                        return
                    }
                }
            } catch is CancellationError {} catch {
                if generation == scope {
                    await cancel()
                    fail()
                }
            }
        }
    }
    private func verifyBrowser() {
        guard presentation.state == "browserConfirmationRequired", let client, let attempt else { return }
        let scope = generation
        presentation.state = "working"
        presentation.status = "accountWorking"
        work = Task { [weak self] in
            guard let self else { return }
            do {
                let account = try await client.verifyBrowser(attempt, browserProof: presentation.browserProof)
                guard generation == scope else { return }
                presentation.state = "confirmAccount"
                presentation.status = "accountConfirmAccount"
                presentation.name = account.name
                presentation.email = account.email
            } catch {
                if generation == scope {
                    presentation.state = "browserConfirmationRequired"
                    presentation.status = "accountConfirmationFailed"
                }
            }
        }
    }
    private func confirm() {
        guard presentation.state == "confirmAccount", let client, let attempt else { return }
        presentation.state = "working"
        presentation.status = "accountWorking"
        let scope = generation
        work = Task { [weak self] in
            guard let self else { return }
            do {
                let session = try await client.confirm(attempt, browserProof: presentation.browserProof)
                guard generation == scope else {
                    try? await client.logout(session)
                    return
                }
                device = session
                self.attempt = nil
                presentation.browserProof = ""
                presentation.state = "signedIn"
                presentation.status = "accountSignedIn"
                presentation.name = session.account.name
                presentation.email = session.account.email
                refreshWork = Task { [weak self] in
                    while !Task.isCancelled {
                        do {
                            try await Task.sleep(for: .seconds(480))
                            guard let self, let current = self.device else { return }
                            self.device = try await client.refresh(current)
                        } catch is CancellationError { return } catch {
                            self?.presentation.status = "accountFailed"
                            return
                        }
                    }
                }
            } catch {
                guard generation == scope else { return }
                presentation.state = "confirmAccount"
                presentation.status = "accountConfirmationFailed"
            }
        }
    }
    private func signOut() {
        guard let client, let device, presentation.state == "signedIn" else { return }
        refreshWork?.cancel()
        refreshWork = nil
        presentation.state = "working"
        presentation.status = "accountWorking"
        work = Task { [weak self] in
            guard let self else { return }
            do {
                try await client.logout(device)
                self.device = nil
                reset()
                await client.close()
                self.client = nil
            } catch {
                presentation.state = "signedIn"
                presentation.status = "accountSignOutFailed"
            }
        }
    }
    func dismiss() async { if attempt != nil || presentation.state == "working" && device == nil { await close() } }
    func close() async {
        generation = UUID()
        work?.cancel()
        refreshWork?.cancel()
        await work?.value
        await refreshWork?.value
        await cancel()
        if let client, let device { try? await client.logout(device) }
        await client?.close()
        client = nil
        device = nil
        reset()
    }
    private func cancel() async {
        if let attempt, let client { try? await client.cancel(attempt) }
        attempt = nil
        if device == nil {
            await client?.close()
            client = nil
        }
        reset()
    }
    private func reset() {
        let service = presentation.service
        presentation = AccountPresentation()
        presentation.service = service
    }
    private func fail() {
        presentation.state = "failed"
        presentation.status = "accountFailed"
    }
}
