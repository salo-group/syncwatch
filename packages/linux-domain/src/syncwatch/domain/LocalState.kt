package syncwatch.domain

import java.util.Collections

data class LocalPreferences(
    val theme: String = "system", val language: String = "en", val skipSeconds: Int = 10,
    val previewsEnabled: Boolean = true, val previewCacheMB: Int = 150, val cursorHideDelayMs: Int = 2500,
    val movieVolume: Double = 1.0, val callVolume: Double = 1.0,
    val rememberMovieVolume: Boolean = true, val duckMovieDuringSpeech: Boolean = false,
    val cameraEnabled: Boolean = true, val microphoneEnabled: Boolean = true,
    // Missing in older state files; the selected folder supplies legacy values until Settings saves them.
    val scanSubfolders: Boolean? = null, val automaticRefresh: Boolean? = null,
    val defaultMovieVolume: Double? = null,
) {
    fun validate() {
        if (theme !in setOf("system", "dark", "light") || language !in setOf("en", "ru") || skipSeconds !in 1..600
            || previewCacheMB !in 50..500 || cursorHideDelayMs !in 500..10000
            || (defaultMovieVolume != null && (!defaultMovieVolume.isFinite() || defaultMovieVolume !in 0.0..1.0))
            || !movieVolume.isFinite() || movieVolume !in 0.0..1.0 || !callVolume.isFinite() || callVolume !in 0.0..1.0
        ) throw WatchFailure("invalidLocalState")
    }
}

data class ResumePosition(val mediaId: String, val positionMs: Long)

/** The accepted history is immutable even when the caller supplied a mutable collection. */
class LocalState(
    val version: Int = 1, val preferences: LocalPreferences = LocalPreferences(),
    val selectedMediaId: String? = null, resumePositions: List<ResumePosition> = emptyList(),
) {
    val resumePositions: List<ResumePosition> = Collections.unmodifiableList(ArrayList(resumePositions))
    fun copy(version: Int = this.version, preferences: LocalPreferences = this.preferences,
             selectedMediaId: String? = this.selectedMediaId, resumePositions: List<ResumePosition> = this.resumePositions) =
        LocalState(version, preferences, selectedMediaId, resumePositions)
    fun validate() {
        if (version != 1) throw WatchFailure("unsupportedVersion")
        preferences.validate()
        selectedMediaId?.let(PlaybackScope::validateId)
        if (resumePositions.size > 256 || resumePositions.map { it.mediaId }.toSet().size != resumePositions.size) throw WatchFailure("invalidLocalState")
        resumePositions.forEach {
            PlaybackScope.validateId(it.mediaId)
            if (it.positionMs !in 0..WatchSession.MAXIMUM_POSITION_MS) throw WatchFailure("invalidLocalState")
        }
    }
    fun resumePositionFor(mediaId: String): Long = resumePositions.firstOrNull { it.mediaId == mediaId }?.positionMs ?: 0
    override fun equals(other: Any?): Boolean = other is LocalState && version == other.version && preferences == other.preferences &&
        selectedMediaId == other.selectedMediaId && resumePositions == other.resumePositions
    override fun hashCode(): Int = listOf(version, preferences, selectedMediaId, resumePositions).hashCode()
}
