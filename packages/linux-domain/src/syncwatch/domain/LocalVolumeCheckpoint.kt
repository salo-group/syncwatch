package syncwatch.domain

/** Remembers actual local volume after a successful user command, including delayed output changes. */
class LocalVolumeCheckpoint(private val scope: PlaybackScope) {
    private var active = false
    private var high: ULong? = null
    private var last: Double? = null
    fun submitted() { active = true }
    fun observe(value: PlaybackObservation): Double? {
        if (!active || value.scope != scope || value.status != PlaybackStatus.ready) return null
        val actual = value.volume ?: return null
        if (high?.let { value.sequence <= it } == true) return null
        high = value.sequence
        if (last == actual) return null
        last = actual
        return actual
    }
}
