package syncwatch.room

import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.WebSocket
import java.time.Duration
import java.util.concurrent.CompletableFuture
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.*

/** One room attempt owns all network work; close cancels and joins it. */
class RoomClient private constructor(val address: RoomAddress, internal val authority: RoomAccountAuthority?) {
    constructor(address: RoomAddress) : this(address, null)
    constructor(authority: RoomAccountAuthority) : this(authority.address, authority)
    internal val version: String get() = if (authority == null) "v1" else "v2"
    private val operations = mutableSetOf<Job>()

    private val client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(10)).build()
    private val gate = Any()
    private val requests = mutableSetOf<CompletableFuture<HttpResponse<ByteArray>>>()
    private var eventStream: RoomEventStream? = null
    private var eventConnection: CompletableFuture<WebSocket>? = null
    private var closing: CompletableDeferred<Unit>? = null
    suspend fun create(identity: RoomIdentity, connection: RoomConnection, creationKey: RoomSecret): RoomAdmission {
        if (authority != null) throw RoomWire.bad()
        val body = RoomWire.encode(mapOf("v" to 1L, "clientRunId" to identity.clientRunId, "syncplayUsername" to identity.syncplayUsername,
            "syncplayHost" to connection.host, "syncplayPort" to connection.port, "syncplayRoom" to connection.room))
        return RoomAdmission.decode(request("POST", "v1/rooms", creationKey, body), identity, true)
    }
    suspend fun join(roomId: String, invitation: RoomSecret, identity: RoomIdentity): RoomAdmission {
        if (authority != null) throw RoomWire.bad()
        RoomWire.id(roomId)
        val body = RoomWire.encode(mapOf("v" to 1L, "invitation" to invitation.value, "clientRunId" to identity.clientRunId, "syncplayUsername" to identity.syncplayUsername))
        return RoomAdmission.decode(request("POST", "v1/rooms/$roomId/join", body = body), identity, false)
    }
    suspend fun snapshot(admission: RoomAdmission): RoomBootstrap = RoomBootstrap.decode(request("GET", "$version/rooms/${admission.roomId}/snapshot", admission = admission))
    suspend fun catalog(admission: RoomAdmission, bootstrap: RoomBootstrap, cursor: String): RoomCatalogPage {
        if (bootstrap.snapshot.epoch != admission.bootstrap.snapshot.epoch) throw RoomWire.bad()
        RoomCatalogPosition(cursor, bootstrap)
        return RoomCatalogPage.decode(request("GET", "$version/rooms/${admission.roomId}/catalog", admission = admission, cursor = cursor), bootstrap, cursor)
    }
    /** The caller serializes bootstrap creation with this walk and bounds its overall lifetime. */
    suspend fun catalogView(admission: RoomAdmission, bootstrap: RoomBootstrap): RoomCatalogView {
        if (bootstrap.snapshot.epoch != admission.bootstrap.snapshot.epoch) throw RoomWire.bad()
        return RoomCatalogView.read(bootstrap) { cursor ->
            request("GET", "$version/rooms/${admission.roomId}/catalog", admission = admission, cursor = cursor)
        }
    }
    suspend fun selection(admission: RoomAdmission, bootstrap: RoomBootstrap): RoomSelectionDefinition {
        if (bootstrap.snapshot.epoch != admission.bootstrap.snapshot.epoch) throw RoomWire.bad()
        return RoomSelectionDefinition.decode(request("GET", "$version/rooms/${admission.roomId}/selection", admission = admission,
            generation = bootstrap.snapshot.watchGeneration), bootstrap)
    }
    suspend fun leave(admission: RoomAdmission) {
        val value = RoomWire.parse(request("POST", "$version/rooms/${admission.roomId}/leave", admission = admission))
        if (authority != null) { if (value.keys != setOf("status") || value["status"] != "left") throw RoomWire.bad(); return }
        RoomWire.keys(value, "v", "status")
        if (RoomWire.integer(value["v"], 1) != 1L || value["status"] != "left") throw RoomWire.bad()
    }
    suspend fun execute(admission: RoomAdmission, command: RoomCommand): RoomCommandResult {
        command.verify(admission)
        return RoomCommandResult.decode(request("POST", "$version/rooms/${admission.roomId}/commands", body = command.encodedBytes, admission = admission), command)
    }
    /** Attach immediately after admission, before hashing media. */
    suspend fun openEvents(admission: RoomAdmission): RoomEventStream = owned {
        currentCoroutineContext().ensureActive()
        validate(admission)
        val credential = authority?.credential()
        val stream = RoomEventStream()
        val uri = address.endpoint("$version/rooms/${admission.roomId}/events")
        val events = java.net.URI(if (uri.scheme == "https") "wss" else "ws", uri.rawAuthority, uri.rawPath, null, null)
        val future = synchronized(gate) {
            if (closing != null || eventStream != null) throw RoomProtocolException("closed")
            eventStream = stream
            val builder = client.newWebSocketBuilder().header("Authorization", "Bearer ${credential?.access ?: admission.token!!.value}")
            if (credential != null) builder.header("X-SyncWatch-Member", admission.memberId).header("X-SyncWatch-Run", run(admission))
            builder.connectTimeout(Duration.ofSeconds(20)).buildAsync(events, stream).also { eventConnection = it }
        }
        try {
            future.awaitRoom()
            currentCoroutineContext().ensureActive()
            synchronized(gate) { if (closing != null) throw RoomProtocolException("closed") }
            stream
        } catch (error: Exception) { future.cancel(true); stream.close(); throw error }
    }
    suspend fun close() = withContext(NonCancellable + Dispatchers.IO) {
        var leader = false
        val completion = synchronized(gate) { closing ?: CompletableDeferred<Unit>().also { closing = it; leader = true } }
        if (leader) {
            try {
                val active = synchronized(gate) { operations.toList() }
                active.forEach { it.cancel() }
                val pending = synchronized(gate) { requests.toList() }
                pending.forEach { it.cancel(true) }
                eventConnection?.cancel(true); eventStream?.close()
                active.joinAll()
                client.shutdownNow()
                if (!client.awaitTermination(Duration.ofSeconds(5))) throw RoomProtocolException("transport")
                completion.complete(Unit)
            } catch (_: Exception) { completion.completeExceptionally(RoomProtocolException("transport")) }
        }
        completion.await()
    }
    private fun run(admission: RoomAdmission): String = admission.bootstrap.snapshot.members.firstOrNull { it.memberId == admission.memberId }?.identity?.clientRunId ?: throw RoomWire.bad()
    private fun validate(admission: RoomAdmission) {
        RoomWire.id(admission.roomId); RoomWire.id(admission.memberId); run(admission)
        if (authority == null) {
            if (admission.accountScope != null || admission.token == null) throw RoomWire.bad()
        } else if (admission.accountScope != authority.scope || admission.token != null) throw RoomWire.bad()
    }
    private suspend fun <T> owned(action: suspend () -> T): T = coroutineScope {
        val work = async(start = CoroutineStart.LAZY) { action() }
        synchronized(gate) { if (closing != null) { work.cancel(); throw RoomProtocolException("closed") }; operations.add(work) }
        try { work.start(); work.await() } finally { synchronized(gate) { operations.remove(work) } }
    }
    internal suspend fun request(method: String, path: String, legacyCredential: RoomSecret? = null, body: ByteArray? = null, cursor: String? = null, generation: Long? = null, admission: RoomAdmission? = null, run: String? = null): ByteArray = owned {
        admission?.let(::validate)
        val endpoint = address.endpoint(path)
        val query = generation?.let { "watchGeneration=$it" } ?: cursor?.let { "cursor=$it" }
        val uri = if (query == null) endpoint else java.net.URI(endpoint.scheme, endpoint.rawAuthority, endpoint.path, query, null)
        withTimeout(20_000) {
            var credential = authority?.credential()
            var retried = false
            while (true) {
                val builder = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(20)).header("Accept", "application/json")
                (credential?.access ?: legacyCredential?.value ?: admission?.token?.value)?.let { builder.header("Authorization", "Bearer $it") }
                if (admission != null && authority != null) builder.header("X-SyncWatch-Member", admission.memberId).header("X-SyncWatch-Run", run(admission))
                else if (run != null) { RoomWire.id(run); builder.header("X-SyncWatch-Run", run) }
                if (body != null) builder.header("Content-Type", "application/json")
                val request = builder.method(method, body?.let(HttpRequest.BodyPublishers::ofByteArray) ?: HttpRequest.BodyPublishers.noBody()).build()
                val future = synchronized(gate) {
                    if (closing != null) throw RoomProtocolException("closed")
                    client.sendAsync(request) { info -> RoomBodySubscriber(info.headers().firstValueAsLong("Content-Length").orElse(-1)) }.also { requests.add(it) }
                }
                val response = try { future.awaitRoom() } finally { synchronized(gate) { requests.remove(future) } }
                currentCoroutineContext().ensureActive()
                if (response.uri() != request.uri() || response.headers().firstValue("Content-Type").orElse("").substringBefore(';').trim().lowercase() != "application/json") throw RoomProtocolException("transport")
                if (response.statusCode() == 401 && !retried && credential != null && authority != null) {
                    val current = authority.credential()
                    if (current.access != credential.access) { credential = current; retried = true; continue }
                }
                val bytes = response.body()
                if (response.statusCode() !in 200..299) {
                    val value = RoomWire.parse(bytes); RoomWire.keys(value, *if (authority == null) arrayOf("error") else arrayOf("v", "error"))
                    if (authority != null && RoomWire.integer(value["v"], 2) != 2L) throw RoomWire.bad()
                    val error = RoomWire.obj(value["error"]); RoomWire.keys(error, "code"); val code = RoomWire.string(error["code"])
                    val allowed = setOf("unauthorized", "forbidden", "notFound", "conflict", "expired", "queueFull", "tlsRequired", "malformed", "frameTooLarge", "internal", "snapshotRequired", "duplicate", "staleGeneration", "accountSignInRequired", "admissionRequired", "unavailable")
                    throw RoomProtocolException(if (code in allowed) code else "transport")
                }
                return@withTimeout if (authority == null) bytes else RoomAccessWire.result(bytes)
            }
            @Suppress("UNREACHABLE_CODE") error("Unreachable room response")
        }
    }
}
private suspend fun <T> CompletableFuture<T>.awaitRoom(): T = suspendCancellableCoroutine { continuation ->
    whenComplete { value, error ->
        if (error != null) continuation.resumeWithException(error) else continuation.resume(value)
    }
    continuation.invokeOnCancellation { cancel(true) }
}
