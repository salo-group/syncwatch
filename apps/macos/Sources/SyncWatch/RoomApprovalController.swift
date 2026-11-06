import Foundation
import SyncWatchDesign

struct RoomApprovalPresentation {
    var rows: [RoomAdmissionRow] = []
    var available = false
    var deciding = false
    var notice = ""
}

/// Owner-only side work; an approval-list outage must not end the active watch.
@MainActor final class RoomApprovalController {
    private let membership: RoomMembershipSession
    private let changed: (RoomApprovalPresentation) -> Void
    private var state = RoomApprovalPresentation()
    private var poll: Task<Void, Never>?
    private var decision: Task<Void, Never>?
    private var revision: UInt64 = 0
    private var closed = false
    init(membership: RoomMembershipSession, changed: @escaping (RoomApprovalPresentation) -> Void) {
        self.membership = membership
        self.changed = changed
    }
    func start() {
        guard poll == nil, !closed else { return }
        changed(state)
        poll = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await read()
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
            }
        }
    }
    private func read() async {
        let expected = revision
        do {
            let rows = try await membership.pendingAdmissions().map {
                RoomAdmissionRow(id: $0.id, name: $0.account.name, email: $0.account.email)
            }
            guard !closed, !Task.isCancelled, revision == expected else { return }
            let modified = state.rows != rows || !state.available || !state.notice.isEmpty
            state.rows = rows
            state.available = true
            state.notice = ""
            if modified { changed(state) }
        } catch {
            guard !closed, !Task.isCancelled, revision == expected else { return }
            state.available = false
            state.notice = "roomPendingUnavailable"
            changed(state)
        }
    }
    func decide(_ id: String, approve: Bool) {
        guard !closed, decision == nil, state.available, state.rows.contains(where: { $0.id == id }) else { return }
        revision += 1
        state.deciding = true
        changed(state)
        decision = Task { [weak self] in
            guard let self else { return }
            do { try await membership.decideAdmission(id, approve: approve) } catch {
                if !closed && !Task.isCancelled { state.notice = "roomAdmissionChanged" }
            }
            guard !closed, !Task.isCancelled else { return }
            revision += 1
            await read()
            guard !closed, !Task.isCancelled else { return }
            state.deciding = false
            decision = nil
            changed(state)
        }
    }
    func close() async {
        closed = true
        poll?.cancel()
        decision?.cancel()
        await poll?.value
        await decision?.value
        poll = nil
        decision = nil
    }
}
