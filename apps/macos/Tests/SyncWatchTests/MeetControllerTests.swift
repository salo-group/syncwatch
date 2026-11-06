import Foundation
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor func meetApplicationChecks(host: RoomController, peer: RoomController, service: URL,
    fixtureToken: String, account: AccountProfile, setAccountAvailable: (Bool) -> Void) async throws {
    let gate = MeetBrowserGate(service: service)
    let owner = MeetController(room: host, openBrowser: gate.open)
    let guest = MeetController(room: peer, openBrowser: { _ in Issue.record("Guest browser must remain explicit"); return false })
    do {
        owner.present(); guest.present()
        try await meetSettles { owner.presentation.enabledActions.contains("meetAuthorize") && guest.presentation.status == "meetIdle" }
        #expect(guest.presentation.enabledActions.isDisjoint(with: ["meetAuthorize", "meetStart", "meetEnd"]))
        owner.action("meetAuthorize")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpenAuthorization") }
        owner.action("meetOpenAuthorization"); try await meetSettles { gate.authorization != nil }
        let expiredCode = try await completeMeetBrowser(service: service, authorization: #require(gate.authorization), fixtureToken: fixtureToken, scenario: "expire")
        try await meetSettles { owner.presentation.status == "meetPermissionExpired" && owner.presentation.enabledActions.contains("meetAuthorize") }
        #expect(host.connected && peer.connected && gate.opens == 0 && owner.presentation.name.isEmpty && owner.presentation.email.isEmpty)
        owner.action("editMeetBrowserProof", value: expiredCode); owner.action("meetConfirm")
        #expect(owner.presentation.status == "meetPermissionExpired" && owner.presentation.browserProof.isEmpty)
        gate.authorization = nil
        owner.action("meetAuthorize")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpenAuthorization") }
        #expect(owner.presentation.name.isEmpty && owner.presentation.email.isEmpty)
        owner.action("meetOpenAuthorization")
        try await meetSettles { gate.authorization != nil }
        let code = try await completeMeetBrowser(service: service, authorization: #require(gate.authorization), fixtureToken: fixtureToken, scenario: "denyNextCreate")
        try await meetSettles { owner.presentation.authorizationState == "browserConfirmationRequired" }
        #expect(owner.presentation.name.isEmpty)
        owner.action("editMeetBrowserProof", value: code); owner.action("meetVerify")
        try await meetSettles { owner.presentation.authorizationState == "confirmAuthorization" }
        #expect(owner.presentation.name == account.name && owner.presentation.email == account.email)
        setAccountAvailable(false)
        #expect(owner.presentation.name.isEmpty && owner.presentation.email.isEmpty && !owner.presentation.enabledActions.contains("meetConfirm"))
        setAccountAvailable(true)
        owner.action("meetConfirm")
        try await meetSettles { owner.presentation.enabledActions.contains("meetStart") }
        #expect(owner.presentation.name.isEmpty)
        owner.action("meetStart")
        try await meetSettles { owner.presentation.status == "meetPermissionUnavailable" && owner.presentation.enabledActions.contains("meetAuthorize") }
        #expect(host.connected && peer.connected && !owner.presentation.enabledActions.contains("meetStart") && !guest.presentation.enabledActions.contains("meetAuthorize"))
        gate.authorization = nil
        owner.action("meetAuthorize"); try await meetSettles { owner.presentation.enabledActions.contains("meetOpenAuthorization") }
        owner.action("meetOpenAuthorization"); try await meetSettles { gate.authorization != nil }
        let renewedCode = try await completeMeetBrowser(service: service, authorization: #require(gate.authorization), fixtureToken: fixtureToken)
        try await meetSettles { owner.presentation.authorizationState == "browserConfirmationRequired" }
        owner.action("editMeetBrowserProof", value: renewedCode); owner.action("meetVerify")
        try await meetSettles { owner.presentation.authorizationState == "confirmAuthorization" }
        #expect(owner.presentation.name == account.name && owner.presentation.email == account.email)
        owner.action("meetConfirm"); try await meetSettles { owner.presentation.enabledActions.contains("meetStart") }
        owner.action("meetStart")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpen") && guest.presentation.enabledActions.contains("meetOpen") }
        #expect(!guest.presentation.enabledActions.contains("meetEnd"))
        owner.action("meetEnd"); #expect(owner.presentation.enabledActions.contains("meetConfirmEnd"))
        owner.action("meetCancelEnd"); #expect(owner.presentation.enabledActions.contains("meetEnd"))
        let context = try #require(host.callMembership())
        let unchanged = try await context.owner.callStatus()
        #expect(unchanged.state == "ready")
        owner.action("meetEnd"); owner.action("meetConfirmEnd")
        try await meetSettles { owner.presentation.status == "meetEnded" && guest.presentation.status == "meetEnded" }
        #expect(host.connected && peer.connected)
        owner.action("meetStart")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpen") }
        owner.action("meetRetry")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpen") }
        let beforeRemoteEnd = try await context.owner.callStatus()
        let remoteEnd = try await context.owner.callEndOperation(beforeRemoteEnd)
        var remotelyEnded = try await context.owner.requestCall(remoteEnd)
        let endDeadline = ContinuousClock.now + .seconds(5)
        while remotelyEnded.state == "ending", ContinuousClock.now < endDeadline {
            try await Task.sleep(for: .milliseconds(100)); remotelyEnded = try await context.owner.callStatus()
        }
        #expect(remotelyEnded.state == "ended" && owner.presentation.enabledActions.contains("meetOpen"))
        owner.action("meetOpen")
        try await meetSettles { owner.presentation.status == "meetEnded" }
        #expect(gate.opens == 0)
        owner.action("meetStart")
        try await meetSettles { owner.presentation.enabledActions.contains("meetOpen") }
        owner.action("meetOpen"); owner.action("meetOpen")
        try await meetSettles { gate.entered }
        #expect(gate.opens == 1)
        setAccountAvailable(false)
        #expect(owner.presentation.name.isEmpty && !owner.presentation.enabledActions.contains("meetOpen") && !guest.presentation.enabledActions.contains("meetConfirm"))
        owner.membershipChanged(); guest.membershipChanged()
        let close = Task { await owner.close(); gate.closeFinished = true }
        await Task.yield(); #expect(!gate.closeFinished)
        gate.release(); await close.value
        #expect(owner.presentation.status != "meetHandedOff" && gate.opens == 1)
        print("SYNCWATCH_MEET_APPLICATION_OK synthetic provider, profile fence, owner/guest, end confirmation, explicit single-flight handoff and late scope loss")
    } catch {
        gate.release(); await owner.close(); await guest.close(); throw error
    }
    gate.release(); await owner.close(); await guest.close()
}
@MainActor private final class MeetBrowserGate {
    let service: URL
    var authorization: URL?
    var entered = false, closeFinished = false
    var opens = 0
    private var waiting: CheckedContinuation<Bool, Never>?
    init(service: URL) { self.service = service }
    func open(_ uri: URL) async -> Bool {
        if uri.host == service.host && uri.port == service.port && uri.path == "/auth/google/meet-start" { authorization = uri; return true }
        #expect(uri.host == "meet.google.com")
        opens += 1; entered = true
        return await withCheckedContinuation { waiting = $0 }
    }
    func release() { let value = waiting; waiting = nil; value?.resume(returning: true) }
}
private final class MeetFixtureRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
private func completeMeetBrowser(service: URL, authorization: URL, fixtureToken: String, scenario: String = "accept") async throws -> String {
    let parts = try #require(URLComponents(url: authorization, resolvingAgainstBaseURL: false))
    let id = try #require(parts.queryItems?.first(where: { $0.name == "id" })?.value)
    let url = service.appendingPathComponent("__test/meet-browser")
    var request = URLRequest(url: url); request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONSerialization.data(withJSONObject: ["v": 2, "transactionId": id, "scenario": scenario, "fixtureToken": fixtureToken])
    let config = URLSessionConfiguration.ephemeral
    config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.timeoutIntervalForRequest = 5; config.timeoutIntervalForResource = 5
    let session = URLSession(configuration: config, delegate: MeetFixtureRedirects(), delegateQueue: nil)
    defer { session.invalidateAndCancel() }
    let (bytes, response) = try await session.data(for: request)
    #expect((response as? HTTPURLResponse)?.statusCode == 200 && bytes.count <= 4096 && response.url == url)
    let envelope = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
    let result = try #require(envelope["result"] as? [String: Any])
    return try #require(result["browserProof"] as? String)
}
@MainActor private func meetSettles(_ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now + .seconds(25)
    while !condition() {
        guard ContinuousClock.now < deadline else { throw URLError(.timedOut) }
        try await Task.sleep(for: .milliseconds(20))
    }
}
