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
    check(draft.editableFields == setOf("skipSeconds") && draft.values["skipSeconds"] == "42" && !draft.isValid)
    draft = draft.edit("skipSeconds", "30").cancel()
    check(preferences.state == initial && draft.values["skipSeconds"] == "42")
    draft = draft.edit("skipSeconds", "30")
    check(draft.save(preferences::savePlayerSettings).second)
    val expected = initial.copy(preferences = initial.preferences.copy(skipSeconds = 30))
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
    println("SYNCWATCH_PLAYER_PREFERENCES_OK transactional write, stale checkpoint, restart, future version and failure")
}
