package syncwatch.bridge

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.domain.PlaybackScope
import syncwatch.ipc.Json

internal fun helperReplyChecks(path: Path) {
    val cases = Json.`object`(Json.parse(Files.readString(path)))["cases"] as List<*>
    check(cases.size == 40)
    val scope = PlaybackScope("helper-run", 7)
    for (raw in cases) {
        val fixture = Json.`object`(raw)
        var accepted = false
        try {
            val reply = HelperReply.decode((fixture["frame"] as String).toByteArray(Charsets.UTF_8), scope, 9,
                HelperReplyKind.valueOf(fixture["expectedKind"] as String), fixture["expectedRevision"] as? String, requireAdmission = fixture["requireAdmission"] == true)
            if (fixture["name"] == "rejected") check(reply == HelperReply.Rejected("unavailable", true))
            accepted = true
        } catch (_: IllegalArgumentException) { }
        check(accepted == fixture["accepted"]) { fixture["name"].toString() }
    }
    println("SYNCWATCH_HELPER_REPLIES_OK 40 correlated reply, scope and engine pin cases")
}
