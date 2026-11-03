/// A scope-bound observation retained for an explicit user seek after an interruption.
/// It never advances time, issues playback commands or acknowledges a request.
public struct SharedPlaybackCheckpoint: Sendable {
    private let scope: PlaybackScope
    private var last: PlaybackObservation?
    private var high: UInt64 = 0
    private var checkpoint: PlaybackObservation?

    public init(scope: PlaybackScope) { self.scope = scope }

    public mutating func observe(_ value: PlaybackObservation, available: Bool) {
        guard value.scope == scope, value.sequence >= high else { return }
        high = value.sequence
        if value.ended || value.status == .failed
            || (value.durationMs != nil && checkpoint.map({ $0.durationMs != value.durationMs }) == true)
        {
            clear()
        }
        guard available, checkpoint == nil else { return }
        last =
            value.status == .ready && value.seekable && !value.buffering && !value.ended
                && (value.durationMs ?? 0) > 0 ? value : nil
    }
    public mutating func interrupt() {
        if checkpoint == nil { checkpoint = last }
        last = nil
    }
    public func position(durationMs: Int64) -> Int64? {
        guard let checkpoint, checkpoint.durationMs == durationMs else { return nil }
        return checkpoint.positionMs
    }
    public mutating func clear() {
        last = nil
        checkpoint = nil
    }
}
