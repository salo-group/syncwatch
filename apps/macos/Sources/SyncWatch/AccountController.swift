import AppKit
import SwiftUI
import SyncWatchDesign
import SyncWatchRoom

/// Window-owned authorization, durable refresh checkpoints and independent playback lifetimes.
@MainActor final class AccountController: ObservableObject {
    @Published var presentation = AccountPresentation()
    private let store: AccountSessionStore?
    private var saved: AccountSavedSession?
    private var memoryOnly: Bool
    private var memoryNotice = "accountMemoryOnly"
    private var restoring = false
    private var storageWork = false
    private var initialized = false
    private var client: AccountClient?
    private var attempt: AccountSignIn?
    private var device: AccountDeviceSession?
    private var work: Task<Void, Never>?
    private var refreshWork: Task<Void, Never>?
    private var generation = UUID()
    init(directory: URL? = nil) {
        store = directory.map { AccountSessionStore(directory: $0) }
        memoryOnly = directory == nil
        if !memoryOnly {
            presentation.state = "working"
            presentation.status = "accountRestoring"
            presentation.storageNotice = "accountWillRemember"
        }
    }
    func restore(retry: Bool = false) {
        guard let store, device == nil, attempt == nil, !restoring, !initialized || retry else { return }
        initialized = true
        restoring = true
        let scope = UUID()
        generation = scope
        presentation.state = "working"
        presentation.status = "accountRestoring"
        work = Task { [weak self] in
            guard let self else { return }
            defer { restoring = false }
            do {
                await client?.close()
                client = nil
                guard let record = try await store.read() else {
                    saved = nil
                    memoryOnly = false
                    reset()
                    return
                }
                saved = record
                presentation.service = record.service
                let owner = AccountClient(address: try RoomAddress(record.service))
                client = owner
                let result = try await owner.resume(record)
                guard generation == scope else { return }
                try await checkpoint(result, owner: owner)
                guard generation == scope else { return }
                activate(result, owner: owner)
            } catch is CancellationError {} catch RoomProtocolFailure.rejected("accountSignInRequired") {
                await client?.close()
                client = nil
                do {
                    try await store.forget()
                    saved = nil
                    reset()
                    presentation.status = "accountSignInRequired"
                } catch { blockStorage() }
            } catch {
                if generation == scope {
                    await client?.close()
                    client = nil
                    blockStorage()
                }
            }
        }
    }
    func libraryPresentation(_ source: LibraryPresentation) -> LibraryPresentation {
        var result = source
        result.enabledActions.insert("openAccount")
        return result
    }
    func action(_ action: String, value: String? = nil) {
        switch action {
        case "accountRetryStorage": if presentation.storageBlocked { restore(retry: true) }
        case "accountUseMemory":
            if presentation.storageBlocked && device == nil {
                let previous = client
                client = nil
                memoryOnly = true
                memoryNotice = "accountMemoryWithSaved"
                saved = nil
                presentation.state = "working"
                work = Task {
                    await previous?.close()
                    reset()
                }
            }
        case "accountForgetStorage":
            guard presentation.storageBlocked, device == nil, let store else { return }
            presentation.state = "working"
            presentation.status = "accountWorking"
            storageWork = true
            work = Task {
                defer { storageWork = false }
                do {
                    try await store.forget()
                    saved = nil
                    memoryOnly = false
                    reset()
                } catch { blockStorage() }
            }
        case "editAccountService":
            if !presentation.storageBlocked, ["signedOut", "failed"].contains(presentation.state), let value,
                value.utf16.count <= 256
            {
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
        guard !presentation.storageBlocked, ["signedOut", "failed"].contains(presentation.state), attempt == nil else {
            return
        }
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
                do { try await checkpoint(session, owner: client) } catch {
                    await cancel()
                    blockStorage()
                    return
                }
                guard generation == scope else {
                    try? await client.logout(session)
                    try? await store?.forget()
                    return
                }
                self.attempt = nil
                activate(session, owner: client)
            } catch {
                guard generation == scope else { return }
                presentation.state = "confirmAccount"
                presentation.status = "accountConfirmationFailed"
            }
        }
    }
    private enum CheckpointFailure: Error { case unavailable }
    private func checkpoint(_ session: AccountDeviceSession, owner: AccountClient) async throws {
        let record = try await owner.checkpoint(session)
        do {
            if !memoryOnly {
                guard let store else { throw CheckpointFailure.unavailable }
                try await store.save(record)
            }
        } catch {
            try? await owner.logout(session)
            try? await store?.forget()
            saved = nil
            device = nil
            throw CheckpointFailure.unavailable
        }
        saved = record
    }
    private func activate(_ session: AccountDeviceSession, owner: AccountClient) {
        device = session
        presentation.browserProof = ""
        presentation.state = "signedIn"
        presentation.status = "accountSignedIn"
        presentation.name = session.account.name
        presentation.email = session.account.email
        presentation.storageBlocked = false
        presentation.storageNotice = memoryOnly ? memoryNotice : "accountSavedOnDevice"
        let scope = generation
        refreshWork = Task { [weak self] in
            var delay = 480
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(delay))
                    guard let self, let record = self.saved else { return }
                    let result = try await owner.resume(record)
                    guard generation == scope else { return }
                    try await checkpoint(result, owner: owner)
                    guard generation == scope else { return }
                    device = result
                    delay = 480
                    presentation.status = "accountSignedIn"
                } catch is CancellationError { return } catch is CheckpointFailure {
                    await owner.close()
                    self?.client = nil
                    self?.blockStorage()
                    return
                } catch RoomProtocolFailure.rejected("accountSignInRequired") {
                    guard let self else { return }
                    device = nil
                    saved = nil
                    await owner.close()
                    client = nil
                    do {
                        if !memoryOnly { try await store?.forget() }
                        reset()
                        presentation.status = "accountSignInRequired"
                    } catch { blockStorage() }
                    return
                } catch {
                    guard !Task.isCancelled else { return }
                    self?.presentation.status = "accountUnavailable"
                    delay = 15
                }
            }
        }
    }
    private func signOut() {
        guard let client, presentation.state == "signedIn" else { return }
        let scope = generation
        let previous = refreshWork
        previous?.cancel()
        refreshWork = nil
        presentation.state = "working"
        presentation.status = "accountWorking"
        work = Task { [weak self] in
            guard let self else { return }
            await previous?.value
            guard let device else {
                blockStorage()
                return
            }
            presentation.status = "accountWorking"
            do {
                try await client.logout(device)
                self.device = nil
                if !memoryOnly { try await store?.forget() }
                saved = nil
                reset()
                await client.close()
                self.client = nil
            } catch {
                if self.device == nil {
                    await client.close()
                    self.client = nil
                    blockStorage()
                } else {
                    if generation == scope { activate(device, owner: client) }
                    presentation.status = "accountSignOutFailed"
                }
            }
        }
    }
    func dismiss() async {
        if attempt != nil || presentation.state == "working" && device == nil {
            let wasStorage = restoring || storageWork
            generation = UUID()
            work?.cancel()
            await work?.value
            let blocked = presentation.storageBlocked
            await cancel()
            if wasStorage || blocked { blockStorage() }
        }
    }
    func close() async {
        generation = UUID()
        work?.cancel()
        refreshWork?.cancel()
        await work?.value
        await refreshWork?.value
        if let attempt, let client { try? await client.cancel(attempt) }
        if attempt != nil && saved != nil && !memoryOnly { try? await store?.forget() }
        attempt = nil
        if memoryOnly, let client, let device { try? await client.logout(device) }
        await client?.close()
        client = nil
        device = nil
        saved = nil
        await store?.close()
        reset()
    }
    private func cancel() async {
        if let attempt, let client { try? await client.cancel(attempt) }
        if attempt != nil && saved != nil && !memoryOnly { try? await store?.forget() }
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
        presentation.storageNotice = memoryOnly ? memoryNotice : "accountWillRemember"
    }
    private func blockStorage() {
        device = nil
        presentation.state = "failed"
        presentation.storageBlocked = true
        presentation.status = "accountStorageUnavailable"
        presentation.storageNotice = "accountSavedUnavailable"
        presentation.name = ""
        presentation.email = ""
        presentation.browserProof = ""
    }
    private func fail() {
        presentation.state = "failed"
        presentation.status = "accountFailed"
    }
}
