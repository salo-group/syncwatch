package syncwatch.domain

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.ipc.Json

fun localVolumeCheckpointChecks(fixtures: Path) {
    val corpus = Json.`object`(Json.parse(Files.readString(fixtures.resolve("local-volume-checkpoint.json"))))
    val cases = corpus["cases"] as List<*>
    check(cases.size == 9)
    for (raw in cases) {
        val row = Json.`object`(raw)
        val model = LocalVolumeCheckpoint(PlaybackScope("run-1", 2L))
        for (item in row["steps"] as List<*>) {
            val step = Json.`object`(item)
            if (step["op"] == "submitted") { model.submitted(); continue }
            val owner = PlaybackScope(if (step["owner"] == "foreignRun") "run-2" else "run-1",
                if (step["owner"] == "oldGeneration") 1L else 2L)
            val value = PlaybackObservation(owner, (step["sequence"] as Number).toLong().toULong(),
                PlaybackStatus.valueOf(step["status"] as? String ?: "ready"),
                0, 60000, true, 1.0, false, false, false, 0, 0, 0,
                (step["volume"] as? Number)?.toDouble(), step["muted"] as? Boolean ?: false)
            check(model.observe(value) == (step["expected"] as? Number)?.toDouble()) { row["name"].toString() }
        }
    }
}
