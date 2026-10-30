// SPDX-License-Identifier: Apache-2.0
package syncwatch.design

/** Recognition consumes player input; every resulting request still passes the current capability gates. */
fun PlayerPresentation.dispatchKey(key: String, repeating: Boolean = false, fullscreen: Boolean = false,
    modified: Boolean = false, protectedFocus: Boolean = false, handler: (String, Double?) -> Unit): Boolean {
    if (modified || protectedFocus) return false
    val binding = SyncWatchPlayer.inputBindings.firstOrNull { it.key == key } ?: return false
    if (binding.fullscreenOnly && !fullscreen) return false
    if (repeating && !binding.repeats) return true
    if (binding.action == "setMovieVolume") volumeStep(binding.value ?: 0.0, handler)
    else dispatch(binding.action, binding.value, handler)
    return true
}
fun PlayerPresentation.dispatchWheel(direction: Double, modified: Boolean = false,
    protectedFocus: Boolean = false, handler: (String, Double?) -> Unit): Boolean {
    if (modified || protectedFocus || !direction.isFinite() || direction == 0.0) return false
    volumeStep(if (direction > 0) 1.0 else -1.0, handler)
    return true
}
private fun PlayerPresentation.volumeStep(direction: Double, handler: (String, Double?) -> Unit) {
    if (!isValid) return
    val value = (volume + direction * SyncWatchPlayer.inputVolumeStep).coerceIn(0.0, 1.0)
    if (value != volume) dispatch("setMovieVolume", value, handler)
}
