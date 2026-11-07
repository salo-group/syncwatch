import Foundation
import SyncWatchRoom
import Testing

@testable import SyncWatch

@MainActor @Test(.enabled(if: ProcessInfo.processInfo.environment["SYNCWATCH_ADMISSION_ISOLATION_FIXTURE"] != nil))
func admissionDoesNotWaitForEventBootstrap() async throws {
    let path = try #require(ProcessInfo.processInfo.environment["SYNCWATCH_ADMISSION_ISOLATION_FIXTURE"])
    let fixture = try #require(
        JSONSerialization.jsonObject(with: Data(contentsOf: URL(filePath: path))) as? [String: Any])
    let identityData = try #require(fixture["identity"] as? [String: String])
    let identity = try RoomIdentity(
        clientRunID: #require(identityData["clientRunId"]), syncplayUsername: #require(identityData["syncplayUsername"])
    )
    let admission = try RoomAdmission.decode(
        JSONSerialization.data(withJSONObject: #require(fixture["admission"])), identity: identity, owner: true)
    let address = try RoomAddress(#require(fixture["service"] as? String))
    let client = RoomClient(address: address)
    let events = RoomClient(address: address)
    let owner = RoomMembershipSession(client: client, admission: admission, identity: identity)
    let connecting = Task { try await owner.connectEvents(events, recovering: false) }
    var reading: Task<RoomWatchContext, Error>?
    do {
        let held = try #require(fixture["held"] as? String)
        let deadline = ContinuousClock.now + .seconds(3)
        while !FileManager.default.fileExists(atPath: held) {
            guard ContinuousClock.now < deadline else { throw RoomProtocolFailure.transport }
            try await Task.sleep(for: .milliseconds(10))
        }
        var completed = false
        let task = Task {
            defer { completed = true }
            return try await owner.admissionContext(admission.bootstrap)
        }
        reading = task
        let until = ContinuousClock.now + .seconds(2)
        while !completed && ContinuousClock.now < until { try await Task.sleep(for: .milliseconds(10)) }
        try #require(completed, "Admission waited for the held event bootstrap")
        let context = try await task.value
        #expect(context.definition.watchGeneration == 2 && context.definition.sequence == 8)
        #expect(context.definition.selection?.media.mediaID == "movie")
        for value in try #require(fixture["invalidAnchors"] as? [[String: Any]]) {
            let anchor = try RoomBootstrap.decode(JSONSerialization.data(withJSONObject: value))
            do {
                _ = try await owner.admissionContext(anchor)
                Issue.record("Admission accepted a foreign binding")
            } catch RoomProtocolFailure.malformed {}
        }
        let advanced = try RoomAdmission.decode(
            JSONSerialization.data(withJSONObject: #require(fixture["advancedAdmission"])), identity: identity,
            owner: true)
        let newer = RoomMembershipSession(client: client, admission: advanced, identity: identity)
        do {
            _ = try await newer.admissionContext(admission.bootstrap)
            Issue.record("Admission accepted a selection below the membership sequence floor")
        } catch RoomProtocolFailure.malformed {}
        let unexpected = try #require(fixture["unexpectedBootstrap"] as? String)
        #expect(!FileManager.default.fileExists(atPath: unexpected))
    } catch {
        connecting.cancel()
        await events.close()
        _ = await connecting.result
        reading?.cancel()
        _ = await reading?.result
        await client.close()
        throw error
    }
    connecting.cancel()
    await events.close()
    _ = await connecting.result
    await client.close()
}
