package syncwatch

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.domain.*

internal suspend fun playerPreferencesChecks(scratch: Path) {
    val path = scratch.resolve("player-preferences/local-state.json")
    val disk = FileLocalStateStore(path)
    val initial = LocalState(preferences = LocalPreferences(language = "ru", movieVolume = 0.4, skipSeconds = 42),
        selectedMediaId = "retained-film", resumePositions = listOf(ResumePosition("retained-film", 12345)))
    disk.write(initial)
    val preferences = AppearancePreferences(disk)
    var draft = preferences.playerSettingsEditor()
    check(draft.editableFields == setOf("skipSeconds", "defaultVolume", "rememberVolume") && draft.values["skipSeconds"] == "42" && !draft.isValid)
    draft = draft.edit("skipSeconds", "30").cancel()
    check(preferences.state == initial && draft.values["skipSeconds"] == "42")
    draft = draft.edit("skipSeconds", "30")
    check(draft.save(preferences::savePlayerSettings).second)
    val expected = initial.copy(preferences = initial.preferences.copy(skipSeconds = 30, defaultMovieVolume = preferences.defaultMovieVolume))
    check(preferences.state == expected && AppearancePreferences(disk).state == expected)
    check(preferences.persistWatchState(initial) && AppearancePreferences(disk).state == expected)
    for (value in listOf("", "0", "42", "600", "30.0", "+30", " 30", "30\n"))
        check(!preferences.savePlayerSettings(mapOf("skipSeconds" to value)))
    check(AppearancePreferences(disk).state == expected)
    val future = "{\"version\":2,\"future\":true}"
    Files.writeString(path, future)
    check(!preferences.savePlayerSettings(mapOf("skipSeconds" to "15")))
    check(preferences.state == expected && Files.readString(path) == future)
    val readonly = AppearancePreferences(disk)
    check(!readonly.playerSettingsEditor().canSave && !readonly.savePlayerSettings(mapOf("skipSeconds" to "15")))
    val failed = AppearancePreferences(object : LocalStateStore {
        override fun read() = LocalStateRead(LocalStateStatus.missing)
        override fun write(state: LocalState) { throw java.io.IOException("owned write failure") }
    })
    val result = failed.playerSettingsEditor().edit("skipSeconds", "60").save(failed::savePlayerSettings)
    check(!result.second && result.first.message == "saveFailed" && failed.state.preferences.skipSeconds == 10)
    val volumeDisk = FileLocalStateStore(scratch.resolve("movie-volume/local-state.json"))
    volumeDisk.write(initial)
    check(AppearancePreferences(FileLocalStateStore(scratch.resolve("fresh-volume/local-state.json"))).openingMovieVolume == 0.5)
    val volume = AppearancePreferences(volumeDisk)
    check(volume.openingMovieVolume == 0.4)
    var volumeDraft = volume.playerSettingsEditor().edit("skipSeconds", "10").edit("defaultVolume", "25").edit("rememberVolume", "false")
    check(volumeDraft.save(volume::savePlayerSettings).second && volume.openingMovieVolume == 0.25)
    check(!volume.rememberMovieVolume(0.6) && AppearancePreferences(volumeDisk).openingMovieVolume == 0.25)
    volumeDraft = volume.playerSettingsEditor().edit("rememberVolume", "true")
    check(volumeDraft.save(volume::savePlayerSettings).second && volume.rememberMovieVolume(0.6))
    check(!volume.rememberMovieVolume(0.6) && !volume.rememberMovieVolume(Double.NaN) && !volume.rememberMovieVolume(1.1))
    check(volume.persistWatchState(initial))
    val volumeRestored = AppearancePreferences(volumeDisk)
    check(volumeRestored.openingMovieVolume == 0.6 && volumeRestored.defaultMovieVolume == 0.25)
    check(volumeRestored.state.selectedMediaId == initial.selectedMediaId && volumeRestored.state.resumePositions == initial.resumePositions)
    for (value in listOf("-5", "101", "2", "NaN")) check(!volume.savePlayerSettings(mapOf("skipSeconds" to "10", "defaultVolume" to value)))
    check(runCatching { initial.copy(preferences = initial.preferences.copy(defaultMovieVolume = Double.POSITIVE_INFINITY)).validate() }.isFailure)
    println("SYNCWATCH_PLAYER_PREFERENCES_OK transactional write, stale checkpoint, restart, future version and failure")
}
