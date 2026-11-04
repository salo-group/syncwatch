/// Remembers actual local volume after a successful user command, including delayed output changes.
public struct LocalVolumeCheckpoint: Sendable {
    private let scope: PlaybackScope
    private var active = false
    private var high: UInt64?
    private var last: Double?

    public init(scope: PlaybackScope) { self.scope = scope }
    public mutating func submitted() { active = true }

    public mutating func observe(_ value: PlaybackObservation) -> Double? {
        guard active, value.scope == scope, value.status == .ready, let actual = value.volume,
            high.map({ value.sequence > $0 }) ?? true else { return nil }
        high = value.sequence
        guard last != actual else { return nil }
        last = actual
        return actual
    }
}
