// SPDX-License-Identifier: Apache-2.0
package syncwatch.design.tests

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.design.*
import syncwatch.ipc.Json

private typealias Record = Map<String, Any?>
private fun Any?.record(): Record = Json.`object`(this)
private fun Any?.rows(): List<Record> = (this as List<*>).map { it.record() }
private fun Record.text(key: String) = this[key] as String
private fun Record.number(key: String) = this[key] as Number
private fun Record.flag(key: String) = this[key] as Boolean
private fun Record.strings(key: String) = (this[key] as List<*>).map { it as String }
private fun Record.optionalNumber(key: String) = (this[key] as Number?)?.toDouble()
private fun corpus(root: Path, name: String) =
    Json.parse(Files.readString(root.resolve(name))).record()["cases"].rows()

private fun playerInputChecks(root: Path) {
    val data = Json.parse(Files.readString(root.resolve("player-input.json"))).record()
    val defaults = data["defaults"].record()
    val cases = data["cases"].rows()
    check(cases.size == 34)
    for (row in cases) {
        val value = defaults + row
        val model = PlayerPresentation(state = PlayerVisualState.valueOf(value.text("state")),
            positionMs = value.number("positionMs").toLong(), durationMs = value.number("durationMs").toLong(),
            volume = value.number("volume").toDouble(), skipSeconds = value.number("skipSeconds").toInt(),
            enabledActions = value.strings("enabledActions").toSet())
        val received = mutableListOf<Pair<String, Double?>>()
        val handler: (String, Double?) -> Unit = { action, number -> received.add(action to number); Unit }
        fun flag(key: String) = row[key] == true
        val handled = if (row.text("kind") == "wheel") model.dispatchWheel(row.number("wheel").toDouble(), flag("modified"), flag("protectedFocus"), handler)
            else model.dispatchKey(row.text("key"), flag("repeating"), flag("fullscreen"), flag("modified"), flag("protectedFocus"), handler)
        val expected = row["expected"].rows().map { it.text("action") to it.optionalNumber("value") }
        check(handled == row.flag("handled") && received.size == expected.size && received.zip(expected).all { (actual, wanted) ->
            actual.first == wanted.first && (if (actual.second != null && wanted.second != null) kotlin.math.abs(actual.second!! - wanted.second!!) < .000001 else actual.second == wanted.second)
        }) { row.text("name") }
        check(model.volume == value.number("volume").toDouble() && model.positionMs == value.number("positionMs").toLong())
    }
    for (number in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
        check(!PlayerPresentation.gallery(PlayerVisualState.paused).dispatchWheel(number) { _, _ -> error("Nonfinite wheel emitted input") })
    }
}

private fun playerChecks(root: Path): Int {
    data class Event(val action: String, val value: Double?)
    val cases = corpus(root, "player-actions.json")
    check(cases.size == 19)
    for (fixture in cases) {
        val model = PlayerPresentation(
            state = PlayerVisualState.valueOf(fixture.text("state")),
            positionMs = fixture.number("positionMs").toLong(), durationMs = fixture.number("durationMs").toLong(),
            volume = fixture.number("volume").toDouble(), skipSeconds = fixture.number("skipSeconds").toInt(),
            hasPrevious = fixture.flag("hasPrevious"), hasNext = fixture.flag("hasNext"),
            enabledActions = fixture.strings("enabledActions").toSet(),
        )
        val before = model.copy()
        val events = mutableListOf<Event>()
        for (request in fixture["requests"].rows()) {
            model.dispatch(request.text("action"), request.optionalNumber("value")) { action, value ->
                events += Event(action, value)
            }
        }
        val expected = fixture["expected"].rows().map { Event(it.text("action"), it.optionalNumber("value")) }
        check(events == expected) { fixture.text("name") }
        check(model == before) { "Requests changed observed player facts" }
        check(model.displayedPosition in 0.0..model.displayedDuration)
    }
    val ready = PlayerPresentation.gallery(PlayerVisualState.paused)
    for (value in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
        ready.dispatch("seek", value) { _, _ -> error("Non-finite seek escaped") }
        ready.dispatch("setMovieVolume", value) { _, _ -> error("Non-finite volume escaped") }
    }
    check(!ready.copy(volume = Double.NaN).allows("muteMovie"))
    check(!ready.copy(volume = Double.NaN).allows("seek"))
    check(PlayerPresentation.time(754_000.0) == "12:34")
    check(PlayerPresentation.time(5_400_000.0) == "1:30:00")
    check(PlayerPresentation.time(Double.NaN) == "00:00")
    check(PlayerVisualState.entries.map { it.name } == SyncWatchPlayer.states)
    return cases.size
}

private fun callChecks(root: Path): Int {
    fun Record.request() = CallRequest(text("action"), (this["values"] as List<*>).map { (it as Number).toDouble() })
    val cases = corpus(root, "call-actions.json")
    check(cases.size == 45)
    for (fixture in cases) {
        val model = CallPresentation(
            state = CallVisualState.valueOf(fixture.text("state")),
            preview = CallPreviewState.valueOf(fixture.text("preview")), joined = fixture["joined"] as Boolean?,
            microphoneEnabled = fixture["microphoneEnabled"] as Boolean?, cameraEnabled = fixture["cameraEnabled"] as Boolean?,
            volume = fixture.optionalNumber("volume"), enabledActions = fixture.strings("enabledActions").toSet(),
        )
        val before = model.copy()
        val events = mutableListOf<CallRequest>()
        for (request in fixture["requests"].rows()) model.dispatch(request.request()) { events += it }
        check(events == fixture["expected"].rows().map { it.request() }) { fixture.text("name") }
        check(model == before) { "Requests changed observed call facts" }
        check(model.showsRemoteSurface == fixture.flag("showsRemote"))
        check(model.showsLocalPreview == fixture.flag("showsLocal"))
        check(model.previewLabel(false) != "cameraActive")
        check(model.displayedVolume in 0.0..1.0)
    }
    val ready = CallPresentation.gallery(SyncWatchCall.galleryCases[2])
    for (value in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
        for (request in listOf(CallRequest("move", listOf(value, 0.0)), CallRequest("resize", listOf(300.0, value)),
            CallRequest("setCallVolume", listOf(value)), CallRequest("setCameraEnabled", listOf(value)))) {
            ready.dispatch(request) { error("Non-finite call request escaped") }
        }
        val invalid = ready.copy(volume = value)
        check(!invalid.allows("setCallVolume") && invalid.displayedVolume == 0.0)
    }
    check(CallVisualState.entries.map { it.name } == SyncWatchCall.states)
    check(CallPreviewState.entries.map { it.name } == SyncWatchCall.previewStates)
    return cases.size
}

private fun selectionChecks(root: Path): Int {
    fun Record.request() = SelectionMenuRequest(text("kind"), text("sourceID"), number("revision").toLong(),
        text("entryID"), text("action"), text("scope"))
    val cases = corpus(root, "selection-menu.json")
    check(cases.isNotEmpty())
    for (fixture in cases) {
        val p = fixture["presentation"].record()
        val model = SelectionMenuPresentation(p.text("kind"), p.text("sourceID"), p.number("revision").toLong(),
            p["entries"].rows().map { SelectionMenuEntry(it.text("id"), it.text("label"), it.flag("enabled"), it["disabledTrack"] as? Boolean ?: false) },
            p["selectedID"] as String?, p.flag("enabled"))
        val before = model.copy()
        check(model.isValid == fixture.flag("valid")) { fixture.text("name") }
        val events = mutableListOf<SelectionMenuRequest>()
        for (request in fixture["requests"].rows()) {
            model.dispatch(request.text("entryID"), request.text("contextToken")) { events += it }
        }
        check(events == fixture["expected"].rows().map { it.request() }) { fixture.text("name") }
        check(model == before) { "Requests changed the observed selection" }
        if (fixture["labels"] != null) {
            fun labels(n: DesignElement): List<String> = (if (n.id.endsWith(".selectionLabel")) listOf(n.text("text")) else emptyList()) + n.children.flatMap { labels(it) }
            for ((language, expected) in fixture["labels"].record()) {
                val tree = checkNotNull(DesignTree.resolve("SelectionMenu", SelectionMenuBindings.context(model), language, 420.0, 360.0))
                check(labels(tree) == expected)
            }
        }
    }
    return cases.size
}

private fun libraryChecks() {
    val events = mutableListOf<String>()
    val receive: (String, String?) -> Unit = { action, value -> events += "$action:${value.orEmpty()}" }
    LibraryPresentation().dispatch("chooseFolder", handler = receive)
    for (state in LibraryVisualState.entries) {
        val model = LibraryPresentation.gallery(state)
        model.dispatch("openCall", handler = receive)
        model.dispatch("selectMovie", "removed-id", receive)
        model.dispatch("startWatching", handler = receive)
    }
    check(events == listOf("startWatching:"))
    LibraryPresentation.gallery(LibraryVisualState.scanning).dispatch("search", "film", receive)
    val selected = LibraryPresentation.gallery(LibraryVisualState.selected)
    selected.dispatch("selectMovie", "1", receive)
    selected.copy(selectedID = "removed-id").dispatch("startWatching", handler = receive)
    selected.copy(enabledActions = selected.enabledActions + "unregistered").dispatch("unregistered", handler = receive)
    check(events == listOf("startWatching:", "selectMovie:1"))
    check(SyncWatchCopy.text("library", "ru") == "Библиотека фильмов")
    check(SyncWatchCopy.text("library", "unknown") == SyncWatchCopy.text("library"))
    check(SyncWatchCopy.text("unknown-key", "ru") == "unknown-key")
    check(LibraryVisualState.entries.map { it.name } == SyncWatchCopy.galleryStates)
}

private fun settingsChecks(root: Path): Int {
    var editor = SettingsEditor(editableFields = setOf("theme", "language"), canSave = true)
    editor = editor.edit("folder", "ignored").edit("unregistered", "ignored")
    check(editor.values["folder"] == "" && "unregistered" !in editor.values)
    var writes = 0
    val invalid = editor.edit("theme", "invalid").save { writes++; true }
    check(!invalid.second && writes == 0 && invalid.first.state == SettingsVisualState.invalidConfiguration)
    editor = invalid.first.cancel().edit("theme", "dark")
    val refused = editor.save { false }
    check(!refused.second && refused.first.message == "saveFailed")
    val saved = refused.first.save { writes++; it["theme"] == "dark" }
    check(saved.second && saved.first.state == SettingsVisualState.savedNotice && writes == 1)
    check(saved.first.edit("theme", "light").cancel().values["theme"] == "dark")
    check(!SettingsEditor().save { writes++; true }.second && writes == 1)
    check(!SettingsEditor(editableFields = setOf("username"), canSave = true).edit("username", "bad\u007F").isValid)
    check(!SettingsEditor(values = mapOf("unexpected" to "value"), canSave = true).isValid)
    check(SettingsVisualState.entries.map { it.name } == SyncWatchSettings.states)

    val fields = Json.parse(Files.readString(root.resolve("player-settings.json"))).record()["fields"].rows()
    check(fields.map { it.text("id") }.toSet() == SyncWatchPlayerSettings.defaults.keys)
    for (fixture in fields) {
        val field = SyncWatchPlayerSettings.fields.single { it.id == fixture.text("id") }
        for (value in fixture.strings("valid")) check(PlayerSettingsEditor.valid(value, field)) { "${field.id}: $value" }
        for (value in fixture.strings("invalid")) check(!PlayerSettingsEditor.valid(value, field)) { "${field.id}: $value" }
    }
    var player = PlayerSettingsEditor(editableFields = SyncWatchPlayerSettings.defaults.keys, canSave = true)
    player = player.edit("brightness", "0.35").edit("subtitleSize", "36").reset("image").edit("unknown", "ignored")
    check(player.values["brightness"] == "0" && player.values["subtitleSize"] == "36" && "unknown" !in player.values)
    val bad = player.edit("cursorDelay", "NaN").save { writes++; true }
    check(!bad.second && writes == 1)
    player = bad.first.cancel().edit("skipSeconds", "30")
    val failed = player.save { false }
    check(!failed.second && failed.first.message == "saveFailed" && failed.first.values["skipSeconds"] == "30")
    var received: Map<String, String>? = null
    val acknowledged = failed.first.save { received = it; writes++; true }
    check(acknowledged.second && writes == 2 && acknowledged.first.message == "changesSaved")
    player = acknowledged.first.edit("skipSeconds", "60")
    check(received?.get("skipSeconds") == "30" && player.cancel().values["skipSeconds"] == "30")
    val limited = PlayerSettingsEditor(values = mapOf("contrast" to "0.5"), editableFields = setOf("brightness"), canSave = true)
        .edit("brightness", "0.2").edit("contrast", "0.4").reset("image")
    check(limited.values["brightness"] == "0" && limited.values["contrast"] == "0.5")
    check(!limited.canReset("audio") && !limited.canReset("unknown"))
    val readonly = PlayerSettingsEditor(editableFields = SyncWatchPlayerSettings.defaults.keys)
        .edit("brightness", "0.5").reset("image")
    check(readonly.state == PlayerSettingsVisualState.readOnly && readonly.values["brightness"] == "0")
    check(!readonly.save { writes++; true }.second && writes == 2)
    check(!PlayerSettingsEditor(editableFields = setOf("brightness"), canSave = true, state = PlayerSettingsVisualState.readOnly).allows("brightness"))
    check(!PlayerSettingsEditor(values = mapOf("unknown" to "value")).isValid)
    check(!PlayerSettingsEditor(editableFields = setOf("unknown"), canSave = true).isValid)
    check(PlayerSettingsEditor.canonical(0.35000000000001) == "0.35")
    check(PlayerSettingsEditor.canonical(-0.0) == "0" && PlayerSettingsEditor.canonical(Double.NaN) == "")
    check(PlayerSettingsVisualState.entries.map { it.name } == SyncWatchPlayerSettings.states)
    return fields.size
}

fun main(arguments: Array<String>) {
    require(arguments.size == 1) { "Pass the shared design fixture directory" }
    val root = Path.of(arguments.single())
    playerInputChecks(root)
    val player = playerChecks(root)
    val call = callChecks(root)
    val selection = selectionChecks(root)
    for (row in corpus(root, "library-metadata.json")) {
        val base = LibraryPresentation.gallery(LibraryVisualState.selected)
        val p = base.copy(
            entries = base.entries.map { it.copy(durationMs = (row["durationMs"] as Number?)?.toLong()) },
            metadataDurationMs = (row["durationMs"] as Number?)?.toLong(), metadataWidth = row.number("width").toInt(),
            metadataHeight = row.number("height").toInt(), metadataAudioTracks = (row["audioTracks"] as Number?)?.toInt(),
            metadataSubtitleTracks = (row["subtitleTracks"] as Number?)?.toInt(),
            metadataAudioName = row["audioName"] as String?, metadataSubtitleName = row["subtitleName"] as String?,
            metadataSubtitlesDisabled = row["subtitlesDisabled"] == true, metadataLoading = row["loading"] == true,
            enabledActions = if (row["selectable"] == true) SyncWatchCopy.libraryActions else base.enabledActions)
        val context = DesignBindings.library(p, "", row.text("language"))
        check(context.collections["entries"]?.first()?.values?.get("rowDuration") == row.text("rowDuration"))
        for ((key, value) in row["expected"].record()) check(context.values[key] == value) { "metadata-presentation-$key" }
    }
    libraryChecks()
    roomChecks()
    val fields = settingsChecks(root)
    println("SYNCWATCH_DESIGN_PRESENTATIONS_OK: $player player, $call call, $selection selection cases; library capabilities; settings and $fields player-setting fields")
}
