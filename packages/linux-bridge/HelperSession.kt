package syncwatch.bridge

import java.util.Collections
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import syncwatch.domain.CorrectionDisposition
import syncwatch.domain.PlaybackActionFence
import syncwatch.domain.PlaybackObservation
import syncwatch.domain.PlaybackPort
import syncwatch.domain.PlaybackScope
import syncwatch.domain.PlaybackStatus
import syncwatch.domain.WatchFailure
import syncwatch.ipc.Json

data class SharedMedia(val handle: String, val name: String, val durationMs: Long, val fileSizeBytes: Long)
data class SyncplayEndpoint(val host: String, val port: Int, val room: String, val username: String)
data class SyncplayParticipant(val username: String, val ready: Boolean)
enum class HelperConnection { starting, connecting, reconnecting, connected, failed, stopped }
data class HelperSessionState(val connection: HelperConnection = HelperConnection.starting, val ready: Boolean = false,
    val acceptedUsername: String? = null, val participants: List<SyncplayParticipant> = emptyList(), val identityRejected: Boolean = false, val playbackReady: Boolean = false)

/** Admit matching media first. The supplied single-thread owner context serializes
 * UI intents, one writer and immutable observations; Syncplay retains timing policy.
 */
class HelperSession(
    private val transport: HelperTransport, private val player: PlaybackPort, private val nativeScope: PlaybackScope,
    private val endpoint: SyncplayEndpoint, private val media: SharedMedia, private val revision: String,
    owner: CoroutineScope, private val requireDeclaredIdentity: Boolean = false, private val admissionProvider: (suspend () -> SyncplayAdmission)? = null, private val changed: (HelperSessionState) -> Unit = {},
) {
    val wireScope = PlaybackScope.fresh().replacingMedia()
    var state = HelperSessionState()
        private set
    private val ownerThread = Thread.currentThread().threadId()
    private val scope = CoroutineScope(owner.coroutineContext + SupervisorJob(owner.coroutineContext[Job]))
    private val admissionNonces = ArrayDeque<String>()
    private var pendingAdmissionNonce: String? = null
    private val commands = HelperCommands(wireScope)
    private val fence = PlaybackActionFence(nativeScope)
    private sealed interface Intent {
        data class Pause(val value: Boolean) : Intent
        data object Toggle : Intent
        data class Seek(val positionMs: Long) : Intent
        data class PlayFrom(val positionMs: Long) : Intent
    }
    private val users = ArrayDeque<Pair<Long, Intent>>()
    private val inbox = ArrayDeque<ByteArray>()
    private val corrections = mutableListOf<PlayerCorrection>()
    private val completed = mutableListOf<Pair<PlayerCorrection, ByteArray>>()
    private data class ReplyRecord(val id: Long, val expected: HelperReplyKind, val reply: HelperReply)
    private val replies = mutableListOf<ReplyRecord>()
    private var commandSequence = 0L
    private var correctionHigh = 0L
    private var eventSequence = 0L
    private var observationHigh = 0uL
    private var heartbeat = true
    private var observed = false
    private var readyRequested = false
    private var requestingReady = false
    private var readinessObserved = false
    private var recoveryIdentity = false
    private var recoveryConnected = false
    private var recoveryDeadline: Long? = null
    private var started = false
    private var closed = false
    private var deadline: Long? = null
    private var wake: CompletableDeferred<Unit>? = null
    private var reader: Job? = null
    private var timer: Job? = null
    private var worker: Job? = null
    private var applying: Deferred<PlaybackObservation>? = null
    private var termination: Deferred<Unit>? = null

    init {
        commands.connect(1, endpoint.host, endpoint.port, endpoint.room, endpoint.username, accountAdmission = admissionProvider != null)
        commands.loaded(1, media.handle, media.name, media.durationMs, media.fileSizeBytes)
        if (media.durationMs <= 0 || !revision.matches(Regex("[0-9a-f]{40}"))) bad()
    }
    fun start() {
        checkOwner(); if (started || closed) bad(); started = true
        reader = scope.launch {
            try {
                while (!closed) {
                    val frame = transport.read().awaitBridge()
                    if (closed) return@launch
                    checkOwner(); if (inbox.size >= 64) throw WatchFailure("queueFull")
                    inbox.addLast(frame); signal()
                }
            } catch (_: Exception) { if (!closed) stop(true) }
        }
        timer = scope.launch {
            try {
                while (!closed) {
                    checkOwner()
                    if (deadline?.let { System.nanoTime() - it >= 0 } == true ||
                        recoveryDeadline?.let { System.nanoTime() - it >= 0 } == true) { stop(true); return@launch }
                    heartbeat = true; signal(); delay(100)
                }
            } catch (_: CancellationException) { }
        }
        worker = scope.launch { try { run() } catch (_: Exception) { if (!closed) stop(true) } }
    }
    fun pause(value: Boolean) = enqueue(Intent.Pause(value))
    fun togglePause() = enqueue(Intent.Toggle)
    fun seek(positionMs: Long) {
        if (positionMs !in 0..media.durationMs) bad()
        enqueue(Intent.Seek(positionMs))
    }
    fun playFrom(positionMs: Long) {
        if (positionMs !in 0..media.durationMs) bad()
        enqueue(Intent.PlayFrom(positionMs))
    }
    private fun enqueue(intent: Intent) {
        checkOwner(); if (closed || state.connection != HelperConnection.connected) bad()
        if (users.size >= 64) throw WatchFailure("queueFull")
        users.addLast(fence.beginUserAction(nativeScope) to intent); applying?.cancel(); signal()
    }
    suspend fun close() {
        checkOwner(); if (!closed) stop(false)
        withContext(NonCancellable) { termination?.await() }
    }
    private fun checkOwner() { check(Thread.currentThread().threadId() == ownerThread) { "Wrong session owner" } }
    private fun signal() { val current = wake; wake = null; current?.complete(Unit) }
    private suspend fun checkOpen() { currentCoroutineContext().ensureActive(); if (closed) bad() }
    private fun setDeadline(seconds: Long) { deadline = System.nanoTime() + seconds * 1_000_000_000 }
    private fun nextId(): Long { if (commandSequence == PlaybackScope.MAXIMUM_GENERATION) bad(); return ++commandSequence }
    private fun publish(value: HelperSessionState) { state = value; changed(state) }
    private suspend fun waitSignal() {
        checkOpen(); val next = CompletableDeferred<Unit>(); wake = next; next.await(); checkOpen()
    }
    private suspend fun send(data: ByteArray) {
        checkOpen(); setDeadline(5); transport.send(data).awaitBridge(); deadline = null; checkOpen()
    }
    private suspend fun request(expected: HelperReplyKind, build: (Long) -> ByteArray) {
        val id = nextId(); send(build(id)); val expires = System.nanoTime() + 5_000_000_000
        try {
            while (true) {
                checkOpen(); deadline = expires
                if (inbox.isEmpty()) { waitSignal(); continue }
                val data = inbox.removeFirst(); val value = PlayerCorrection.frameValue(data)
                if (value["replyTo"] == "cmd-$id") {
                    val reply = HelperReply.decode(data, wireScope, id, expected, revision, requireAdmission = admissionProvider != null)
                    if (reply is HelperReply.Rejected) bad()
                    replies += ReplyRecord(id, expected, reply); if (replies.size > 128) replies.removeAt(0)
                    return
                }
                receive(data, value)
            }
        } finally { deadline = null }
    }
    private suspend fun run() {
        setDeadline(2)
        validate(player.observe(nativeScope)); setDeadline(2); player.setPaused(true, nativeScope)
        while (!player.observe(nativeScope).paused) delay(20)
        deadline = null
        request(HelperReplyKind.ready, commands::hello)
        request(HelperReplyKind.applied) { commands.loaded(it, media.handle, media.name, media.durationMs, media.fileSizeBytes) }
        publish(state.copy(connection = HelperConnection.connecting))
        request(HelperReplyKind.applied) { commands.connect(it, endpoint.host, endpoint.port, endpoint.room, endpoint.username, accountAdmission = admissionProvider != null) }
        while (!closed) {
            checkOpen()
            while (inbox.isNotEmpty()) { val data = inbox.removeFirst(); receive(data, PlayerCorrection.frameValue(data)) }
            val nonce = pendingAdmissionNonce
            if (nonce != null && admissionProvider != null) {
                pendingAdmissionNonce = null; setDeadline(5)
                val credential = admissionProvider(); checkOpen(); deadline = null
                request(HelperReplyKind.applied) { commands.admit(it, nonce, credential) }; continue
            }
            cancelSuperseded()
            if (users.isNotEmpty()) { val next = users.removeFirst(); user(next.first, next.second); continue }
            val first = corrections.firstOrNull()
            if (first != null && fence.correction(nativeScope, first.afterUserAction) == CorrectionDisposition.apply) { correct(first); continue }
            if (heartbeat) {
                heartbeat = false; setDeadline(2); val value = player.observe(nativeScope); deadline = null; validate(value)
                // VLC sequences identify events, not reads. Fresh paused samples
                // must reach Syncplay after login even with an unchanged event sequence.
                if (value.sequence >= observationHigh) {
                    send(commands.observation(nextId(), value, nativeScope)); observationHigh = value.sequence; observed = true
                }
            }
            if ((state.connection == HelperConnection.connected || recoveryConnected) && observed && !readyRequested) {
                requestingReady = true
                request(HelperReplyKind.applied) { commands.ready(it, true) }; readyRequested = true
                requestingReady = false; finishReadiness(); continue
            }
            if (inbox.isEmpty() && users.isEmpty() && !heartbeat) waitSignal()
        }
    }
    private suspend fun reconnect() {
        // Do not replay unpublished intents or old timing corrections across transports.
        if (users.isNotEmpty() || fence.issuedSequence != fence.publishedSequence ||
            corrections.any { it.operation != PlayerCorrection.Operation.Pause(true) }) bad()
        if (recoveryDeadline == null) recoveryDeadline = System.nanoTime() + 25_000_000_000L
        recoveryIdentity = false; recoveryConnected = false; observed = false
        readyRequested = false; requestingReady = false; readinessObserved = false
        publish(state.copy(connection = HelperConnection.reconnecting, ready = false, playbackReady = false, participants = emptyList()))
        setDeadline(2)
        try {
            player.setPaused(true, nativeScope)
            while (true) {
                val value = player.observe(nativeScope); validate(value)
                if (value.paused) break
                delay(20)
            }
        } finally { deadline = null }
        heartbeat = true
    }
    private fun finishReadiness() {
        if (!observed || !readyRequested || !(readinessObserved || state.ready)) return
        if (recoveryDeadline != null) {
            if (!recoveryConnected) return
            recoveryDeadline = null; recoveryConnected = false
            state = state.copy(connection = HelperConnection.connected)
        }
        if (state.connection != HelperConnection.connected) return
        publish(state.copy(playbackReady = true))
    }
    private suspend fun user(sequence: Long, intent: Intent) {
        setDeadline(2)
        val pause = when (intent) { is Intent.Pause -> intent.value; is Intent.PlayFrom -> false; Intent.Toggle -> !player.observe(nativeScope).paused; else -> null }
        val position = when (intent) { is Intent.Seek -> intent.positionMs; is Intent.PlayFrom -> intent.positionMs; else -> null }
        if (position != null) player.seek(position, nativeScope)
        if (pause != null) player.setPaused(pause, nativeScope)
        setDeadline(2)
        try {
            while (true) {
                checkOpen(); val actual = player.observe(nativeScope); validate(actual)
                if ((pause == null || pause == actual.paused) && (position == null || kotlin.math.abs(position - actual.positionMs) <= 250)) {
                    if (!fence.canPublish(sequence, nativeScope)) return
                    send(commands.userObservation(nextId(), sequence, actual, nativeScope))
                    fence.didPublish(sequence, actual); observationHigh = actual.sequence; return
                }
                delay(20)
            }
        } finally { deadline = null }
    }
    private suspend fun correct(command: PlayerCorrection) {
        val task = scope.async { command.apply(player, nativeScope, media.handle) }
        applying = task; setDeadline(2)
        try {
            val actual = task.await(); checkOpen()
            if (fence.correction(nativeScope, command.afterUserAction) == CorrectionDisposition.apply) {
                val data = command.result(actual, nativeScope); send(data)
                observationHigh = maxOf(observationHigh, actual.sequence); complete(command, data)
            }
        } catch (error: CancellationException) { if (fence.issuedSequence <= command.afterUserAction) throw error }
        finally { applying = null; deadline = null }
    }
    private suspend fun cancelSuperseded() {
        for (command in corrections.filter { it.afterUserAction < fence.publishedSequence }) {
            val data = command.cancelledReply(); send(data); complete(command, data)
        }
    }
    private fun complete(command: PlayerCorrection, data: ByteArray) {
        corrections.removeAll { it.id == command.id }; completed += command to data
        if (completed.size > 128) completed.removeAt(0)
    }
    private suspend fun receive(data: ByteArray, value: Map<String, Any?>) {
        val kind = PlayerCorrection.text(value, "type")
        if (value.containsKey("replyTo")) {
            val record = replies.firstOrNull { value["replyTo"] == "cmd-${it.id}" } ?: bad()
            if (HelperReply.decode(data, wireScope, record.id, record.expected, revision, requireAdmission = admissionProvider != null) != record.reply) bad()
            return
        }
        if (kind.startsWith("player.")) {
            val command = PlayerCorrection.decode(data, wireScope)
            val record = completed.firstOrNull { it.first.id == command.id }
            if (record != null) { if (!record.first.sameRequest(command)) bad(); send(record.second); return }
            val pending = corrections.firstOrNull { it.id == command.id }
            if (pending != null) { if (!pending.sameRequest(command)) bad(); return }
            val number = command.id.drop(4).toLongOrNull() ?: bad()
            if (number <= correctionHigh || command.afterUserAction > fence.issuedSequence || corrections.size >= 64) bad()
            correctionHigh = number; corrections += command; return
        }
        PlayerCorrection.keys(value, setOf("v", "id", "type", "clientRunId", "mediaGeneration", "payload"))
        val eventId = PlayerCorrection.text(value, "id"); val ordinal = eventId.removePrefix("event-").toLongOrNull() ?: bad()
        if (PlayerCorrection.integer(value["v"], 1) != 1L || eventId != "event-$ordinal" || ordinal != eventSequence + 1) bad()
        wireScope.check(PlaybackScope(PlayerCorrection.text(value, "clientRunId"), PlayerCorrection.integer(value["mediaGeneration"], PlaybackScope.MAXIMUM_GENERATION)))
        eventSequence = ordinal; val payload = Json.`object`(value["payload"])
        when (kind) {
            "sync.admissionRequired" -> {
                PlayerCorrection.keys(payload, setOf("nonce")); val nonce = text(payload["nonce"], 32)
                if (admissionProvider == null || pendingAdmissionNonce != null || state.connection !in setOf(HelperConnection.connecting, HelperConnection.reconnecting) ||
                    !nonce.matches(Regex("[0-9a-f]{32}")) || nonce in admissionNonces) bad()
                if (admissionNonces.size == 128) admissionNonces.removeFirst()
                admissionNonces.addLast(nonce); pendingAdmissionNonce = nonce
            }
            "sync.identity" -> {
                PlayerCorrection.keys(payload, setOf("username")); val username = text(payload["username"], 64)
                if (requireDeclaredIdentity && username != endpoint.username) { state = state.copy(identityRejected = true); bad() }
                if (state.acceptedUsername != null && state.acceptedUsername != username) { state = state.copy(identityRejected = true); bad() }
                state = state.copy(acceptedUsername = username)
                if (recoveryDeadline != null) recoveryIdentity = true
            }
            "sync.connection" -> {
                PlayerCorrection.keys(payload, setOf("state"), setOf("error"))
                if (payload.containsKey("error")) {
                    val wrapped = (Json.write(mapOf("v" to 1L, "replyTo" to "cmd-1", "type" to "bridge.error", "clientRunId" to wireScope.clientRunId,
                        "mediaGeneration" to wireScope.mediaGeneration, "payload" to payload["error"])) + "\n").toByteArray(Charsets.UTF_8)
                    HelperReply.decode(wrapped, wireScope, 1, HelperReplyKind.applied)
                }
                when (payload["state"]) {
                    "connecting" -> {
                        if (state.connection in setOf(HelperConnection.connected, HelperConnection.reconnecting)) reconnect()
                        else state = state.copy(connection = HelperConnection.connecting)
                    }
                    "connected" -> {
                        if (state.acceptedUsername == null) bad()
                        if (recoveryDeadline != null) { if (!recoveryIdentity) bad(); recoveryConnected = true; observed = false; heartbeat = true }
                        else state = state.copy(connection = HelperConnection.connected)
                    }
                    else -> bad()
                }
            }
            "sync.readiness" -> {
                PlayerCorrection.keys(payload, setOf("ready")); state = state.copy(ready = payload["ready"] as? Boolean ?: bad())
                if (state.ready && observed && (requestingReady || readyRequested)) readinessObserved = true
                if (recoveryDeadline != null && state.ready) {
                    if (!recoveryConnected || !observed || !(requestingReady || readyRequested)) bad()
                }
                finishReadiness()
            }
            "sync.participants" -> {
                PlayerCorrection.keys(payload, setOf("participants")); val rows = payload["participants"] as? List<*> ?: bad()
                if (rows.size > 64) bad()
                val people = rows.map { raw -> val row = Json.`object`(raw); PlayerCorrection.keys(row, setOf("username", "ready"))
                    SyncplayParticipant(text(row["username"], 64), row["ready"] as? Boolean ?: bad()) }
                state = state.copy(participants = Collections.unmodifiableList(people))
            }
            else -> bad()
        }
        changed(state)
    }
    private fun validate(value: PlaybackObservation) {
        nativeScope.check(value.scope)
        if (value.status != PlaybackStatus.ready || value.durationMs != media.durationMs || (!value.seekable && !(value.ended && value.paused))) bad()
    }
    private fun text(value: Any?, maximum: Int): String {
        val text = value as? String ?: bad()
        if (text.codePointCount(0, text.length) !in 1..maximum || text.any { it.code < 32 || it.code == 127 }) bad()
        return text
    }
    private fun stop(failed: Boolean) {
        checkOwner(); if (closed) return; closed = true; fence.invalidate(); users.clear(); inbox.clear(); corrections.clear()
        publish(state.copy(connection = if (failed) HelperConnection.failed else HelperConnection.stopped, ready = false, playbackReady = false))
        applying?.cancel(); scope.cancel(); signal()
        termination = CoroutineScope(SupervisorJob() + Dispatchers.IO).async {
            try { player.setPaused(true, nativeScope) } catch (_: Exception) { }
            transport.close(); reader?.cancel(); worker?.join(); reader?.join()
        }
    }
    private fun bad(): Nothing = throw WatchFailure("unavailable")
}

private suspend fun <T> CompletableFuture<T>.awaitBridge(): T = suspendCancellableCoroutine { continuation ->
    whenComplete { value, error ->
        if (error == null) continuation.resume(value)
        else continuation.resumeWithException(if (error is CompletionException) error.cause ?: error else error)
    }
    continuation.invokeOnCancellation { cancel(true) }
}
