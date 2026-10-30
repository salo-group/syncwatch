package syncwatch.domain

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.ipc.Json

fun sharedPlaybackCheckpointChecks(fixtures: Path) {
    val corpus = Json.`object`(Json.parse(Files.readString(fixtures.resolve("shared-playback-checkpoint.json"))))
    val cases = corpus["cases"] as List<*>
    check(cases.size == 18)
    for (raw in cases) {
        val row = Json.`object`(raw)
        val model = SharedPlaybackCheckpoint(PlaybackScope("run-1", 2L))
        for (item in row["steps"] as List<*>) {
            val step = Json.`object`(item)
            when (step["op"]) {
                "interrupt" -> model.interrupt()
                "clear" -> model.clear()
                "observe" -> {
                    val owner = PlaybackScope(if (step["scope"] == "foreignRun") "run-2" else "run-1",
                        if (step["scope"] == "oldGeneration") 1L else 2L)
                    val observation = PlaybackObservation(owner, (step["sequence"] as? Number)?.toLong()?.toULong() ?: 1uL,
                        PlaybackStatus.valueOf(step["status"] as? String ?: "ready"),
                        (step["positionMs"] as? Number)?.toLong() ?: 49000,
                        if (step.containsKey("durationMs")) (step["durationMs"] as? Number)?.toLong() else 60000,
                        step["paused"] as? Boolean ?: true, 1.0, step["seekable"] as? Boolean ?: true,
                        step["buffering"] as? Boolean ?: false, step["ended"] as? Boolean ?: false,
                        0, 0, 0, null, false)
                    model.observe(observation, step["available"] as? Boolean ?: true)
                }
                else -> error("Unknown checkpoint operation")
            }
        }
        check(model.position(60000) == (row["expectedPositionMs"] as? Number)?.toLong()) { row["name"].toString() }
        check(model.position(90000) == null)
    }
}
