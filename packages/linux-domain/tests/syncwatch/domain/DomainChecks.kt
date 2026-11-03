package syncwatch.domain

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.ipc.Json

private fun number(value: Any?): Long = (value as Number).toLong()
private fun effect(value: WatchEffect): String = when (value) {
    is WatchEffect.Load -> "load:${value.scope.clientRunId}:${value.scope.mediaGeneration}:${value.mediaId}:${value.positionMs}"
    is WatchEffect.Release -> "release:${value.scope.clientRunId}:${value.scope.mediaGeneration}"
}
private fun requireCase(value: Boolean, name: String) { check(value) { name } }

/** Executes the same language-neutral state and lifecycle corpus as macOS and Windows. */
fun main(arguments: Array<String>) {
    sharedPlaybackCheckpointChecks(Path.of(arguments[0]).toAbsolutePath().parent)
    mediaAgreementChecks(Path.of(arguments[0]).toAbsolutePath().parent)
    val corpus = Json.`object`(Json.parse(Files.readString(Path.of(arguments.single()))))
    actionFenceChecks(Path.of(arguments.single()).parent)
    val directory = Files.createTempDirectory("syncwatch-domain-")
    try {
        val store = FileLocalStateStore(directory.resolve("nested/state.json"))
        requireCase(store.read().status == LocalStateStatus.missing, "missing")
        val stateCases = corpus["localStateCases"] as List<*>
        Files.createDirectories(store.path.parent)
        for (item in stateCases) {
            val fixture = Json.`object`(item)
            val bytes = (fixture["json"] as? String ?: Json.write(fixture["value"])).toByteArray(Charsets.UTF_8)
            Files.write(store.path, bytes)
            requireCase(store.read().status.name == fixture["status"], fixture["name"].toString())
        }
        val lifecycleCases = corpus["lifecycleCases"] as List<*>
        for (item in lifecycleCases) {
            val fixture = Json.`object`(item)
            var session = WatchSession(scope = PlaybackScope("run-1", fixture["initialGeneration"]?.let(::number) ?: 0))
            for (raw in fixture["steps"] as List<*>) {
                val step = Json.`object`(raw); val before = session.localState; val beforeScope = session.scope; val beforeId = session.mediaId
                var effects = emptyList<WatchEffect>(); var errorCode: String? = null
                try {
                    val owner = PlaybackScope(step["ownerRunId"] as? String ?: session.scope.clientRunId,
                        step["ownerGeneration"]?.let(::number) ?: session.scope.mediaGeneration)
                    when (step["op"]) {
                        "select" -> effects = session.select(step["mediaId"] as String)
                        "start" -> effects = session.start()
                        "loaded" -> session.loaded(owner)
                        "observe" -> session.recordPlayback(owner, number(step["positionMs"]), step["durationMs"]?.let(::number), step["paused"] as? Boolean ?: true, step["ended"] as? Boolean ?: false)
                        "home" -> session.returnToLibrary()
                        "continue" -> session.continueWatching()
                        "end" -> effects = session.end()
                        "cancel" -> effects = session.cancel(owner)
                        "preferences" -> session.setPreferences(session.localState.preferences.copy(
                            movieVolume = (step["movieVolume"] as Number).toDouble(), callVolume = (step["callVolume"] as Number).toDouble()))
                        "restart" -> session = WatchSession(session.localState, PlaybackScope("run-2", 0))
                        else -> error("Unknown fixture operation")
                    }
                } catch (failure: WatchFailure) { errorCode = failure.code }
                val name = "${fixture["name"]}/${step["op"]}"
                requireCase(errorCode == step["error"], "$name error $errorCode")
                if (errorCode != null) requireCase(session.localState == before && session.scope == beforeScope && session.mediaId == beforeId, "$name transaction")
                val expected = Json.`object`(step["expected"])
                requireCase(session.phase.name == expected["phase"] && session.scope.mediaGeneration == number(expected["generation"])
                    && session.positionMs == number(expected["positionMs"]) && session.paused == expected["paused"]
                    && session.playerPresented == expected["playerPresented"] && effects.map(::effect) == expected["effects"], name)
            }
        }
        Files.delete(store.path)
        val session = WatchSession(); session.select("movie-a"); session.start(); session.loaded(session.scope)
        session.recordPlayback(session.scope, 12345, 50000, true)
        session.setPreferences(LocalPreferences(movieVolume = .25, callVolume = 0.0, cameraEnabled = false))
        store.write(session.localState)
        requireCase(store.read().state == session.localState, "roundtrip")
        val bytes = Files.readAllBytes(store.path)
        try { store.write(session.localState.copy(preferences = LocalPreferences(movieVolume = Double.NaN))); error("invalid write accepted") }
        catch (_: WatchFailure) { }
        requireCase(Files.readAllBytes(store.path).contentEquals(bytes), "failed write preserves destination")
        for (invalid in listOf(byteArrayOf(123), ByteArray(FileLocalStateStore.MAXIMUM_BYTES + 1) { 32 })) {
            Files.write(store.path, invalid)
            requireCase(store.read().status == LocalStateStatus.invalid && Files.readAllBytes(store.path).contentEquals(invalid), "invalid read preserves bytes")
        }
        val future = "{\"version\":2,\"futureSetting\":true}".toByteArray()
        Files.write(store.path, future)
        try { store.write(LocalState()); error("future overwritten") } catch (failure: WatchFailure) { requireCase(failure.code == "unsupportedVersion", "future code") }
        requireCase(Files.readAllBytes(store.path).contentEquals(future), "future preserved")
        val history = mutableListOf(ResumePosition("movie-a", 1)); val frozen = LocalState(resumePositions = history)
        history.clear(); requireCase(frozen.resumePositions.size == 1, "caller collection frozen")
        for (i in 0..256) {
            session.select("movie-$i"); session.start(); session.loaded(session.scope); session.recordPlayback(session.scope, i + 1L, null, true)
        }
        requireCase(session.localState.resumePositions.size == 256 && session.localState.resumePositionFor("movie-0") == 0L, "bounded history")
        requireCase(stateCases.size == 31 && lifecycleCases.size == 7, "complete corpus")
        println("SYNCWATCH_LINUX_DOMAIN_OK ${stateCases.size} state cases; ${lifecycleCases.size} lifecycle scenarios; atomic persistence, ownership and bounded history")
    } finally {
        Files.walk(directory).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach(Files::delete) }
    }
}
