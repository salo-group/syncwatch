package syncwatch.domain

/** Retains an actual observation for an explicit user seek; never becomes a playback clock. */
class SharedPlaybackCheckpoint(private val scope: PlaybackScope) {
    private var last: PlaybackObservation? = null
    private var high = 0uL
    private var checkpoint: PlaybackObservation? = null

    fun observe(value: PlaybackObservation, available: Boolean) {
        if (value.scope != scope || value.sequence < high) return
        high = value.sequence
        if (value.ended || value.status == PlaybackStatus.failed ||
            value.durationMs != null && checkpoint?.let { it.durationMs != value.durationMs } == true) clear()
        if (!available || checkpoint != null) return
        last = value.takeIf { it.status == PlaybackStatus.ready && it.seekable && !it.buffering && !it.ended && (it.durationMs ?: 0) > 0 }
    }
    fun interrupt() {
        if (checkpoint == null) checkpoint = last
        last = null
    }
    fun position(durationMs: Long): Long? = checkpoint?.takeIf { it.durationMs == durationMs }?.positionMs
    fun clear() { last = null; checkpoint = null }
}
