package syncwatch.domain

import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.NoSuchFileException
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.StandardOpenOption
import syncwatch.ipc.Json

enum class LocalStateStatus { missing, loaded, invalid, unsupportedVersion }
data class LocalStateRead(val status: LocalStateStatus, val state: LocalState? = null)
interface LocalStateStore { fun read(): LocalStateRead; fun write(state: LocalState) }

/** The application chooses an owned file and serializes calls outside rendering. */
class FileLocalStateStore(path: Path) : LocalStateStore {
    val path: Path = path.toAbsolutePath().normalize()
    override fun read(): LocalStateRead {
        val bytes = try { Files.newInputStream(path).use { it.readNBytes(MAXIMUM_BYTES + 1) } }
            catch (_: NoSuchFileException) { return LocalStateRead(LocalStateStatus.missing) }
        if (bytes.size > MAXIMUM_BYTES) return LocalStateRead(LocalStateStatus.invalid)
        return try {
            val text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
            val root = Json.`object`(Json.parse(text))
            val version = integer(root["version"])
            if (version !in 0..Int.MAX_VALUE.toLong()) return LocalStateRead(LocalStateStatus.invalid)
            if (version != 1L) return LocalStateRead(LocalStateStatus.unsupportedVersion)
            val state = decode(root); state.validate(); LocalStateRead(LocalStateStatus.loaded, state)
        } catch (_: IllegalArgumentException) { LocalStateRead(LocalStateStatus.invalid) }
          catch (_: java.nio.charset.CharacterCodingException) { LocalStateRead(LocalStateStatus.invalid) }
    }
    override fun write(state: LocalState) {
        state.validate()
        if (read().status == LocalStateStatus.unsupportedVersion) throw WatchFailure("unsupportedVersion")
        val bytes = Json.write(encode(state)).toByteArray(StandardCharsets.UTF_8)
        if (bytes.size > MAXIMUM_BYTES) throw WatchFailure("invalidLocalState")
        Files.createDirectories(path.parent)
        val temporary = Files.createTempFile(path.parent, ".local-state-", ".tmp")
        try {
            FileChannel.open(temporary, StandardOpenOption.WRITE).use { output ->
                val buffer = ByteBuffer.wrap(bytes)
                while (buffer.hasRemaining()) output.write(buffer)
                output.force(true)
            }
            Files.move(temporary, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temporary) }
    }
    companion object {
        const val MAXIMUM_BYTES = 65536
        private fun integer(value: Any?): Long = value as? Long ?: throw WatchFailure("invalidLocalState")
        private fun flag(value: Any?): Boolean = value as? Boolean ?: throw WatchFailure("invalidLocalState")
        private fun text(value: Any?): String = value as? String ?: throw WatchFailure("invalidLocalState")
        private fun decimal(value: Any?): Double = (value as? Number)?.toDouble() ?: throw WatchFailure("invalidLocalState")
        private fun int(value: Any?): Int {
            val number = integer(value)
            if (number !in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()) throw WatchFailure("invalidLocalState")
            return number.toInt()
        }
        private fun decode(root: Map<String, Any?>): LocalState {
            val p = Json.`object`(root["preferences"])
            val preferences = LocalPreferences(
                text(p["theme"]), text(p["language"]), int(p["skipSeconds"]), flag(p["previewsEnabled"]),
                int(p["previewCacheMB"]), int(p["cursorHideDelayMs"]), decimal(p["movieVolume"]), decimal(p["callVolume"]),
                flag(p["rememberMovieVolume"]), flag(p["duckMovieDuringSpeech"]), flag(p["cameraEnabled"]), flag(p["microphoneEnabled"]),
                p["scanSubfolders"]?.let(::flag), p["automaticRefresh"]?.let(::flag),
                p["defaultMovieVolume"]?.let(::decimal),
            )
            val resumes = root["resumePositions"] as? List<*> ?: throw WatchFailure("invalidLocalState")
            val selected = root["selectedMediaId"]?.let(::text)
            return LocalState(1, preferences, selected, resumes.map {
                val row = Json.`object`(it); ResumePosition(text(row["mediaId"]), integer(row["positionMs"]))
            })
        }
        private fun encode(state: LocalState): Map<String, Any?> {
            val p = state.preferences
            return mapOf("version" to state.version, "selectedMediaId" to state.selectedMediaId,
                "preferences" to mapOf("theme" to p.theme, "language" to p.language, "skipSeconds" to p.skipSeconds,
                    "previewsEnabled" to p.previewsEnabled, "previewCacheMB" to p.previewCacheMB, "cursorHideDelayMs" to p.cursorHideDelayMs,
                    "defaultMovieVolume" to p.defaultMovieVolume, "movieVolume" to p.movieVolume, "callVolume" to p.callVolume, "rememberMovieVolume" to p.rememberMovieVolume,
                    "duckMovieDuringSpeech" to p.duckMovieDuringSpeech, "cameraEnabled" to p.cameraEnabled, "microphoneEnabled" to p.microphoneEnabled,
                    "scanSubfolders" to p.scanSubfolders, "automaticRefresh" to p.automaticRefresh).filterValues { it != null },
                "resumePositions" to state.resumePositions.map { mapOf("mediaId" to it.mediaId, "positionMs" to it.positionMs) })
        }
    }
}
