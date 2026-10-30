// SPDX-License-Identifier: Apache-2.0
package syncwatch.design

import androidx.compose.runtime.*
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.ui.Modifier
import kotlin.math.round


// Observed player state and capability-checked local requests.
enum class PlayerVisualState { loading, paused, playing, buffering, fullscreen, connectionInterrupted, ended }
data class PlayerPresentation(
    val state: PlayerVisualState = PlayerVisualState.loading,
    val title: String = "",
    val roomNotice: String = "",
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val volume: Double = 1.0,
    val muted: Boolean = false,
    val skipSeconds: Int = 10,
    val hasPrevious: Boolean = false,
    val hasNext: Boolean = false,
    val enabledActions: Set<String> = emptySet(),
) {
    val isValid: Boolean get() = durationMs in 0..SyncWatchPlayer.maximumPositionMs && positionMs in 0..durationMs.coerceAtLeast(0) && volume.isFinite() && volume in 0.0..1.0 && skipSeconds in 1..600
    val playing: Boolean get() = state == PlayerVisualState.playing || state == PlayerVisualState.fullscreen
    val displayedDuration: Double get() = durationMs.coerceIn(0, SyncWatchPlayer.maximumPositionMs).toDouble()
    val displayedPosition: Double get() = positionMs.coerceAtLeast(0).toDouble().coerceAtMost(displayedDuration)
    fun allows(action: String): Boolean = action in enabledActions && action in SyncWatchPlayer.actions && when (action) {
        "playPause", "seek", "skip", "previousFile", "nextFile", "selectAudio", "selectSubtitles" -> isValid && (state in setOf(PlayerVisualState.paused, PlayerVisualState.playing, PlayerVisualState.fullscreen) || state == PlayerVisualState.ended && action in SyncWatchPlayer.completedNavigationActions) && when (action) {
            "seek", "skip" -> durationMs > 0
            "previousFile" -> hasPrevious
            "nextFile" -> hasNext
            else -> true
        }
        "setMovieVolume", "muteMovie" -> isValid
        else -> true
    }
    fun dispatch(action: String, value: Double? = null, handler: (String, Double?) -> Unit) {
        if (!allows(action)) return
        when (action) {
            "seek" -> if (value == null || !value.isFinite() || round(value) != value || value !in 0.0..durationMs.toDouble()) return
            "skip" -> {
                if (value !in listOf(-1.0, 1.0) || !allows("seek")) return
                val target = (positionMs + value!! * skipSeconds * 1000).coerceIn(0.0, durationMs.toDouble())
                if (target != positionMs.toDouble()) handler("seek", target)
                return
            }
            "setMovieVolume" -> if (value == null || !value.isFinite() || value !in 0.0..1.0) return
            else -> if (value != null) return
        }
        handler(action, value)
    }
    companion object {
        fun time(milliseconds: Double): String {
            val seconds = (if (milliseconds.isFinite()) milliseconds else 0.0).coerceIn(0.0, SyncWatchPlayer.maximumPositionMs.toDouble()).toLong() / 1000
            fun pad(n: Long) = n.toString().padStart(2, '0')
            return if (seconds >= 3600) "${seconds / 3600}:${pad(seconds / 60 % 60)}:${pad(seconds % 60)}" else "${pad(seconds / 60)}:${pad(seconds % 60)}"
        }
        fun gallery(state: PlayerVisualState) = PlayerPresentation(state = state, title = SyncWatchCopy.galleryMovies[1],
            durationMs = if (state == PlayerVisualState.loading) 0 else 5400000,
            positionMs = if (state == PlayerVisualState.loading) 0 else if (state == PlayerVisualState.ended) 5400000 else 754000,
            volume = 0.7, hasPrevious = true, hasNext = true, enabledActions = SyncWatchPlayer.actions)
    }
}

@Composable fun PlayerChrome(presentation:PlayerPresentation=PlayerPresentation(),language:String="en",theme:SyncWatchTheme=SyncWatchTheme.dark,modifier:Modifier=Modifier,
    videoSurface:@Composable BoxScope.()->Unit={},selectionMenu:SelectionMenuPresentation?=null,
    onSelection:(SelectionMenuRequest)->Unit={},onDismissSelection:()->Unit={},onAction:(String,Double?)->Unit={_,_->}) {
    NativePlayerInput(presentation, selectionMenu != null, onAction)
    val slots=mutableMapOf<String,@Composable BoxScope.()->Unit>("video" to videoSurface)
    selectionMenu?.let { menu -> slots[menu.kind+"Selection"]={SelectionMenu(menu,language,theme,onSelection=onSelection)} }
    DesignSurface("Player",DesignBindings.player(presentation),language,theme,modifier,slots){event->
        if(event.action=="dismissSelection")onDismissSelection() else presentation.dispatch(event.action,event.value?.toDoubleOrNull(),onAction)
    }
}
