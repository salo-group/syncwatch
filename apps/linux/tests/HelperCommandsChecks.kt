package syncwatch.bridge

import java.nio.file.Files
import java.nio.file.Path
import syncwatch.domain.PlaybackObservation
import syncwatch.domain.PlaybackScope
import syncwatch.domain.PlaybackStatus
import syncwatch.ipc.Json

internal fun helperCommandChecks(path: Path) {
    val cases = Json.`object`(Json.parse(Files.readString(path)))["cases"] as List<*>
    check(cases.size == 31)
    val encoder = HelperCommands(PlaybackScope("helper-run", 7))
    val native = PlaybackScope("native-owner", 2)
    for (raw in cases) {
        val fixture = Json.`object`(raw); val args = Json.`object`(fixture["args"])
        fun number(key: String, fallback: Long) = (args[key] as? Number)?.toLong() ?: fallback
        fun text(key: String, fallback: String) = args[key] as? String ?: fallback
        val id = number("id", 9)
        var encoded: Any? = null
        try {
            val loading = args["loading"] == true
            val actual = PlaybackObservation(if (args["foreignNative"] == true) PlaybackScope("foreign", 2) else native,
                1u, if (loading) PlaybackStatus.loading else PlaybackStatus.ready, 1300, if (loading) null else 4000,
                true, 1.0, !loading, false, false, 0, 0, 0, .25, true)
            val frame = when (fixture["op"]) {
                "hello" -> encoder.hello(id); "shutdown" -> encoder.shutdown(id); "disconnect" -> encoder.disconnect(id); "ready" -> encoder.ready(id, true)
                "loaded" -> encoder.loaded(id, text("handle", "media-1"), text("name", "Кино 🎬.mkv"), number("durationMs", 4000), number("fileSizeBytes", 4096))
                "connect" -> encoder.connect(id, text("host", "127.0.0.1"), number("port", 8999).toInt(), text("room", "room"), text("username", "Viewer"), accountAdmission = args["accountAdmission"] == true)
                "admit" -> encoder.admit(id, text("nonce", "a".repeat(32)), SyncplayAdmission(text("ticket", "A".repeat(64)), text("clientRunId", "room-run")))
                "observation" -> encoder.observation(id, actual, native); "user" -> encoder.userObservation(id, number("sequence", 3), actual, native)
                else -> error("unknown command fixture")
            }
            check(frame.last() == 10.toByte() && frame.size <= 65536)
            encoded = Json.parse(frame.toString(Charsets.UTF_8))
        } catch (_: IllegalArgumentException) { }
        check(equivalent(encoded, fixture["expected"])) { fixture["name"].toString() }
    }
    println("SYNCWATCH_HELPER_COMMANDS_OK 31 golden command and observation authority cases")
}

private fun equivalent(left: Any?, right: Any?): Boolean = when {
    left is Number && right is Number -> left.toDouble() == right.toDouble()
    left is Map<*, *> && right is Map<*, *> -> left.keys == right.keys && left.all { (key, value) -> equivalent(value, right[key]) }
    left is List<*> && right is List<*> -> left.size == right.size && left.zip(right).all { equivalent(it.first, it.second) }
    else -> left == right
}
