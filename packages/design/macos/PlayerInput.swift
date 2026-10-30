import Foundation

extension PlayerPresentation {
    /// Returns whether this input belongs to the player; requests still pass observation/capability gates.
    public func dispatchKey(
        _ key: String, repeating: Bool = false, fullscreen: Bool = false,
        modified: Bool = false, protectedFocus: Bool = false, to handler: (String, Double?) -> Void
    ) -> Bool {
        guard !modified, !protectedFocus,
            let binding = SyncWatchPlayer.inputBindings.first(where: { $0.key == key }),
            !binding.fullscreenOnly || fullscreen
        else { return false }
        guard !repeating || binding.repeats else { return true }
        if binding.action == "setMovieVolume" {
            dispatchVolumeStep(binding.value ?? 0, to: handler)
        } else {
            dispatch(binding.action, value: binding.value, to: handler)
        }
        return true
    }

    public func dispatchWheel(
        _ direction: Double, modified: Bool = false, protectedFocus: Bool = false,
        to handler: (String, Double?) -> Void
    ) -> Bool {
        guard !modified, !protectedFocus, direction.isFinite, direction != 0 else { return false }
        dispatchVolumeStep(direction > 0 ? 1 : -1, to: handler)
        return true
    }

    private func dispatchVolumeStep(_ direction: Double, to handler: (String, Double?) -> Void) {
        guard isValid else { return }
        let value = min(1, max(0, volume + direction * SyncWatchPlayer.inputVolumeStep))
        if value != volume { dispatch("setMovieVolume", value: value, to: handler) }
    }
}
