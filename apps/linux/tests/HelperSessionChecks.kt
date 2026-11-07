package syncwatch.bridge

import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import syncwatch.domain.*
import syncwatch.ipc.Json

private const val SESSION_REVISION = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

/** Scripted IPC peer with a user-write gate; it does not decide playback timing. */
private class SessionPeer : HelperTransport {
    private var context = emptyMap<String, Any?>()
    private val incoming = ArrayDeque<ByteArray>()
    private val sent = mutableListOf<Map<String, Any?>>()
    private var reading: CompletableFuture<ByteArray>? = null
    private var writing: CompletableFuture<Unit>? = null
    private var event = 0L
    private var ended = false
    private var holdUser = false
    private var stationaryHandshake = false
    private var samples = 0
    var ticketMode = false
    var advertiseAdmission = true
    private var automaticReady = true
    private var pulseBeforeAck: Boolean? = null
    @Volatile var userWriteHeld = false
        private set
    private fun base(kind: String, payload: Map<String, Any?>): MutableMap<String, Any?> = mutableMapOf(
        "v" to 1L, "type" to kind, "payload" to payload, "clientRunId" to context["clientRunId"],
        "mediaGeneration" to context["mediaGeneration"])
    private fun push(value: Map<String, Any?>) {
        val data = (Json.write(value) + "\n").toByteArray(Charsets.UTF_8)
        val current = reading; reading = null
        if (current != null) current.complete(data) else incoming.addLast(data)
    }
    private fun emit(kind: String, payload: Map<String, Any?>) {
        push(base(kind, payload).also { it["id"] = "event-${++event}" })
    }
    @Synchronized override fun send(frame: ByteArray): CompletableFuture<Unit> {
        val value = PlayerCorrection.frameValue(frame); context = value
        if (value["origin"] == "user" && holdUser) {
            userWriteHeld = true
            return CompletableFuture<Unit>().also { writing = it }.thenApply { finish(value) }
        }
        finish(value); return CompletableFuture.completedFuture(Unit)
    }
    @Synchronized private fun finish(value: Map<String, Any?>) {
        check(!ended); sent.add(value)
        val id = value["id"] ?: return
        val kind = value["type"]
        if (kind == "player.state") {
            if (++samples == 2 && stationaryHandshake) {
                emit("sync.identity", mapOf("username" to "Viewer_2")); emit("sync.connection", mapOf("state" to "connected"))
            }
            return
        }
        val hello = kind == "bridge.hello"
        if (kind == "sync.setReady" && pulseBeforeAck == true) {
            emit("sync.readiness", mapOf("ready" to true)); emit("sync.readiness", mapOf("ready" to false))
        }
        if (kind == "sync.connect" && ticketMode) {
            check(Json.`object`(value["payload"])["accountAdmission"] == true)
            emit("sync.admissionRequired", mapOf("nonce" to "a".repeat(32)))
        }
        push(base(if (hello) "bridge.ready" else "bridge.result",
            if (hello) mapOf("syncplayRevision" to SESSION_REVISION) + if (advertiseAdmission) mapOf("admissionVersion" to 1L) else emptyMap() else mapOf("status" to "applied")).also { it["replyTo"] = id })
        if (kind == "sync.connect" && !ticketMode && !stationaryHandshake || kind == "sync.admit") {
            emit("sync.identity", mapOf("username" to "Viewer_2")); emit("sync.connection", mapOf("state" to "connected"))
        }
        if (kind == "sync.setReady" && automaticReady && pulseBeforeAck != true) {
            emit("sync.readiness", mapOf("ready" to true))
            if (pulseBeforeAck == false) emit("sync.readiness", mapOf("ready" to false))
        }
    }
    @Synchronized override fun read(): CompletableFuture<ByteArray> {
        if (incoming.isNotEmpty()) return CompletableFuture.completedFuture(incoming.removeFirst())
        if (ended) return CompletableFuture.failedFuture(IllegalStateException("EOF"))
        return CompletableFuture<ByteArray>().also { reading = it }
    }
    @Synchronized override fun close() {
        ended = true; reading?.completeExceptionally(IllegalStateException("EOF")); reading = null
        writing?.completeExceptionally(IllegalStateException("EOF")); writing = null
    }
    @Synchronized fun requestAdmission(nonce: String) { emit("sync.admissionRequired", mapOf("nonce" to nonce)) }
    @Synchronized fun admissionCount(): Int = sent.count { it["type"] == "sync.admit" }
    @Synchronized fun admissionMatches(index: Int, ticket: String, nonce: String): Boolean {
        val payload = Json.`object`(sent.filter { it["type"] == "sync.admit" }[index]["payload"])
        return payload["ticket"] == ticket && payload["nonce"] == nonce && payload["clientRunId"] == "member-run"
    }
    @Synchronized fun readiness(value: Boolean) { emit("sync.readiness", mapOf("ready" to value)) }
    @Synchronized fun connection(value: String) { emit("sync.connection", mapOf("state" to value)) }
    @Synchronized fun identity(value: String) { emit("sync.identity", mapOf("username" to value)) }
    @Synchronized fun requireStationaryHandshake() { stationaryHandshake = true }
    @Synchronized fun holdReadiness() { automaticReady = false }
    @Synchronized fun pulseReadiness(beforeAck: Boolean) { pulseBeforeAck = beforeAck }
    @Synchronized fun userMatches(position: Long, paused: Boolean): Boolean {
        val payload = sent.lastOrNull { it["origin"] == "user" }?.get("payload") as? Map<*, *> ?: return false
        return payload["positionMs"] == position && payload["paused"] == paused
    }
    @Synchronized fun readyCount() = sent.count { it["type"] == "sync.setReady" }
    @Synchronized fun gateUserWrite() { holdUser = true }
    @Synchronized fun releaseUserWrite() { holdUser = false; val current = writing; writing = null; current?.complete(Unit) }
    @Synchronized fun correction(id: Long, after: Long, position: Long) {
        push(base("player.setPosition", mapOf("positionMs" to position)).also { it["id"] = "cmd-$id"; it["afterUserAction"] = after })
    }
    @Synchronized fun replyCount(id: Long, kind: String) = sent.count { it["replyTo"] == "cmd-$id" && it["type"] == kind }
    @Synchronized fun sawNativeEnd() = sent.any {
        val payload = it["payload"] as? Map<*, *>
        it["type"] == "player.state" && payload?.get("positionMs") == 60000L && payload["seekable"] == false && payload["paused"] == true
    }
    @Synchronized fun userCount() = sent.count { it["origin"] == "user" }
}

private class SessionPlayer(private val scope: PlaybackScope) : PlaybackPort {
    private var sequence = 1uL
    @Volatile var paused = false
        private set
    var position = 1200L
        private set
    var seeks = 0
        private set
    private var ended = false
    fun finishMedia() { sequence++; ended = true; paused = true; position = 60000 }
    var closes = 0
        private set
    override suspend fun observe(scope: PlaybackScope): PlaybackObservation {
        this.scope.check(scope)
        return PlaybackObservation(scope, sequence, PlaybackStatus.ready, position, 60000, paused, 1.0,
            !ended, false, ended, 0, 0, 0, null, false)
    }
    override suspend fun setPaused(paused: Boolean, scope: PlaybackScope) { if (this.paused != paused) sequence++; this.paused = paused }
    override suspend fun seek(positionMs: Long, scope: PlaybackScope) { sequence++; position = positionMs; seeks++ }
    override suspend fun load(localFile: Path, scope: PlaybackScope): Unit = error("Unexpected load")
    override suspend fun setRate(rate: Double, scope: PlaybackScope): Unit = error("Unexpected rate")
    override suspend fun setVolume(volume: Double, muted: Boolean, scope: PlaybackScope): Unit = error("Unexpected volume")
    override suspend fun tracks(subtitles: Boolean, scope: PlaybackScope): List<PlaybackTrack> = emptyList()
    override suspend fun selectTrack(id: Int, subtitles: Boolean, scope: PlaybackScope): Unit = error("Unexpected track")
    override suspend fun stop(scope: PlaybackScope): Unit = error("Unexpected stop")
    override suspend fun close() { closes++ }
}

private suspend fun eventually(predicate: () -> Boolean) {
    val end = System.nanoTime() + 3_000_000_000
    while (!predicate()) { check(System.nanoTime() < end) { "Session check timeout" }; delay(10) }
}

private suspend fun scenario(owner: CoroutineScope, eof: Boolean, strict: Boolean = false, nativeEnd: Boolean = false) {
    val native = PlaybackScope.fresh().replacingMedia(); val player = SessionPlayer(native); val peer = SessionPeer()
    val session = HelperSession(peer, player, native, SyncplayEndpoint("localhost", 8999, "room", "Viewer"),
        SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner, requireDeclaredIdentity = strict)
    try {
        if (strict) {
            player.setPaused(false, native)
            session.start(); eventually { session.state.connection == HelperConnection.failed }
            check(!session.state.ready && session.state.identityRejected && session.state.acceptedUsername == null && peer.userCount() == 0)
            return
        }
        session.start(); eventually { session.state.ready }
        check(session.state.acceptedUsername == "Viewer_2" && player.paused && peer.userCount() == 0)
        if (nativeEnd) {
            player.finishMedia(); eventually { peer.sawNativeEnd() }
            check(session.state.connection == HelperConnection.connected && session.state.ready && peer.userCount() == 0)
        } else if (eof) {
            session.pause(false); eventually { peer.userCount() == 1 }
            peer.close(); eventually { session.state.connection == HelperConnection.failed }
        } else {
            peer.gateUserWrite(); session.pause(false); eventually { peer.userWriteHeld }
            peer.correction(1, 1, 2400); delay(30)
            check(session.state.connection == HelperConnection.connected && player.seeks == 0)
            peer.releaseUserWrite(); eventually { peer.replyCount(1, "player.result") == 1 }
            peer.correction(1, 1, 2400); eventually { peer.replyCount(1, "player.result") == 2 }
            check(player.seeks == 1)
            peer.readiness(false); eventually { !session.state.ready }
            session.seek(3000); eventually { peer.userCount() == 2 }
            peer.correction(2, 0, 0); eventually { peer.replyCount(2, "bridge.error") == 1 }
            check(player.position == 3000L && peer.userCount() == 2)
            peer.correction(3, 3, 0); eventually { session.state.connection == HelperConnection.failed }
        }
    } finally { session.close(); session.close(); check(player.paused && player.closes == 0) }
    check(player.paused && player.closes == 0)
}

private suspend fun recovery(owner: CoroutineScope, scenario: String) {
    val native = PlaybackScope.fresh().replacingMedia(); val player = SessionPlayer(native); val peer = SessionPeer()
    val session = HelperSession(peer, player, native, SyncplayEndpoint("localhost", 8999, "room", "Viewer_2"),
        SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner, requireDeclaredIdentity = true)
    try {
        session.start(); eventually { session.state.ready }; peer.holdReadiness()
        if (scenario in setOf("pendingIntent", "pendingCorrection")) peer.gateUserWrite()
        session.pause(scenario == "paused")
        if (scenario in setOf("pendingIntent", "pendingCorrection")) {
            eventually { peer.userWriteHeld }
            if (scenario == "pendingIntent") session.seek(4000) else peer.correction(1, 1, 4000)
            peer.connection("connecting"); delay(30); peer.releaseUserWrite()
            eventually { session.state.connection == HelperConnection.failed }; check(peer.userCount() == 1)
        } else {
            eventually { peer.userCount() == 1 }; peer.connection("connecting")
            eventually { session.state.connection == HelperConnection.reconnecting && !session.state.ready }
            eventually { player.paused }; check(session.state.acceptedUsername == "Viewer_2")
            check(runCatching { session.togglePause() }.isFailure); check(runCatching { session.seek(4000) }.isFailure)
            when (scenario) {
                "missingIdentity" -> { peer.connection("connected"); eventually { session.state.connection == HelperConnection.failed } }
                "renamed" -> { peer.identity("Viewer_3"); eventually { session.state.connection == HelperConnection.failed }; check(session.state.identityRejected) }
                "earlyReady" -> { peer.readiness(true); eventually { session.state.connection == HelperConnection.failed } }
                "cancel" -> { session.close(); check(session.state.connection == HelperConnection.stopped) }
                "timeout" -> { delay(20000); peer.connection("connecting"); delay(5300); eventually { session.state.connection == HelperConnection.failed } }
                else -> {
                    for (attempt in 2..3) {
                        peer.identity("Viewer_2"); peer.connection("connected"); eventually { peer.readyCount() == attempt }
                        check(session.state.connection == HelperConnection.reconnecting && !session.state.ready)
                        peer.readiness(true); eventually { session.state.connection == HelperConnection.connected && session.state.ready }
                        session.seek(attempt * 1000L); eventually { peer.userCount() == attempt }
                        if (attempt == 2) { peer.connection("connecting"); eventually { session.state.connection == HelperConnection.reconnecting } }
                    }
                    check(player.position == 3000L)
                }
            }
        }
    } finally { session.close(); session.close(); check(player.paused && player.closes == 0) }
}

private suspend fun readinessPulse(owner: CoroutineScope, beforeAck: Boolean) {
    val native = PlaybackScope.fresh().replacingMedia(); val player = SessionPlayer(native); val peer = SessionPeer()
    peer.pulseReadiness(beforeAck)
    val session = HelperSession(peer, player, native, SyncplayEndpoint("localhost", 8999, "room", "Viewer"),
        SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner)
    try {
        session.start(); eventually { session.state.playbackReady && !session.state.ready }
        check(session.state.connection == HelperConnection.connected)
        check(runCatching { session.playFrom(-1) }.isFailure); check(runCatching { session.playFrom(60001) }.isFailure)
        session.playFrom(22000); eventually { peer.userCount() == 1 }
        check(peer.userMatches(22000, false) && player.position == 22000L && !player.paused && peer.readyCount() == 1)
    } finally { session.close(); check(!session.state.playbackReady && player.closes == 0) }
}

private suspend fun stationaryHandshake(owner: CoroutineScope) {
    val native = PlaybackScope.fresh().replacingMedia(); val player = SessionPlayer(native); val peer = SessionPeer()
    peer.requireStationaryHandshake()
    val session = HelperSession(peer, player, native, SyncplayEndpoint("localhost", 8999, "room", "Viewer"),
        SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner)
    try {
        session.start(); eventually { session.state.playbackReady }
        check(player.paused && player.position == 1200L && peer.userCount() == 0 && player.closes == 0)
    } finally { session.close() }
}

private suspend fun admissionChecks(owner: CoroutineScope) {
    val native = PlaybackScope.fresh().replacingMedia(); var calls = 0
    var peer = SessionPeer().apply { ticketMode = true }
    var session = HelperSession(peer, SessionPlayer(native), native, SyncplayEndpoint("localhost", 8999, "room", "Viewer_2"),
        SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner, requireDeclaredIdentity = true,
        admissionProvider = {
            calls++; if (calls == 2) delay(6000)
            SyncplayAdmission((if (calls == 1) "A" else "B").repeat(64), "member-run")
        })
    try {
        session.start(); eventually { session.state.playbackReady }
        check(calls == 1 && peer.admissionMatches(0, "A".repeat(64), "a".repeat(32)))
        peer.connection("connecting"); peer.requestAdmission("b".repeat(32))
        eventually { calls == 2 }
        check(session.state.connection == HelperConnection.reconnecting && !session.state.playbackReady)
        delay(6000)
        check(session.state.connection != HelperConnection.failed) { "Transport admission ended before its challenge window" }
        eventually { calls == 2 && session.state.playbackReady }
        check(peer.admissionMatches(1, "B".repeat(64), "b".repeat(32)))
        peer.connection("connecting"); peer.requestAdmission("a".repeat(32))
        eventually { session.state.connection == HelperConnection.failed }; check(calls == 2)
    } finally { session.close() }
    for ((missing, expires) in listOf(false to false, true to false, false to true)) {
        calls = 0; var cancelled = false
        peer = SessionPeer().apply { ticketMode = true; advertiseAdmission = !missing }
        session = HelperSession(peer, SessionPlayer(native), native, SyncplayEndpoint("localhost", 8999, "room", "Viewer_2"),
            SharedMedia("media-1", "Movie.mkv", 60000, 1234), SESSION_REVISION, owner,
            admissionProvider = {
                calls++
                try { delay(20_000) } catch (error: kotlinx.coroutines.CancellationException) { cancelled = true; throw error }
                SyncplayAdmission("A".repeat(64), "member-run")
            })
        try {
            session.start(); eventually { if (missing) session.state.connection == HelperConnection.failed else calls == 1 }
            if (expires) {
                delay(11_000)
                check(session.state.connection == HelperConnection.failed && cancelled)
            }
            session.close(); session.close()
            check(calls == (if (missing) 0 else 1) && (missing || cancelled) && peer.admissionCount() == 0)
        } finally { session.close() }
    }
    check(SyncplayAdmission("A".repeat(64), "member-run").toString() == "[redacted]")
}

internal fun helperSessionChecks() {
    Executors.newSingleThreadExecutor().asCoroutineDispatcher().use { dispatcher ->
        runBlocking(dispatcher) {
            admissionChecks(this)
            stationaryHandshake(this)
            for (beforeAck in listOf(false, true)) readinessPulse(this, beforeAck)
            scenario(this, false); scenario(this, true); scenario(this, false, strict = true); scenario(this, false, nativeEnd = true)
            for (scenario in listOf("recover", "paused", "missingIdentity", "renamed", "earlyReady", "pendingIntent", "pendingCorrection", "cancel", "timeout")) recovery(this, scenario)
        }
    }
    println("SYNCWATCH_HELPER_SESSION_OK publication race replay stale correction future authority EOF recovery identity readiness budget cleanup")
}
