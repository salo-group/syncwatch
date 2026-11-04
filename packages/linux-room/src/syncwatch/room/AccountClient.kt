package syncwatch.room

import java.net.URI
import java.net.http.*
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Duration
import java.util.Base64
import java.util.concurrent.CompletableFuture
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.*

data class AccountProfile(val id: String, val name: String, val email: String)
class AccountDeviceSession internal constructor(val account: AccountProfile, internal val access: String, internal val refresh: String) {
    override fun toString() = "[account session redacted]"
}
class AccountSignIn internal constructor(val browserUri: URI, internal val id: String, internal val proof: String) {
    internal val confirmation = AccountClient.secret()
    override fun toString() = "[account sign-in redacted]"
}
data class AccountSignInStatus(val state: String, val account: AccountProfile?)

/** Separate account API; Google credentials never enter the desktop. */
class AccountClient(private val address: RoomAddress) : AutoCloseable {
    private val client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(10)).build()
    private val pending = java.util.concurrent.ConcurrentHashMap.newKeySet<CompletableFuture<*>>()
    private val gate = Any()
    private var closed = false
    suspend fun start(): AccountSignIn {
        val proof = secret()
        val result = request("sign-ins", mapOf("challenge" to base64(MessageDigest.getInstance("SHA-256").digest(proof.toByteArray(Charsets.US_ASCII)))))
        val id = token(result["transactionId"])
        val uri = address.endpoint("auth/google/start?id=$id")
        need(result["browserUri"] == uri.toASCIIString() && result["expiresIn"] == 300L && result["pollAfterMs"] == 2000L)
        return AccountSignIn(uri, id, proof)
    }
    suspend fun poll(signIn: AccountSignIn): AccountSignInStatus {
        val result = request("sign-ins/${signIn.id}/poll", mapOf("proof" to signIn.proof))
        val state = RoomWire.string(result["status"])
        need(state in setOf("pending", "authorizing", "browserConfirmationRequired", "confirmAccount", "complete", "cancelled", "failed"))
        return AccountSignInStatus(state, if (state == "confirmAccount") profile(result["account"], true) else null)
    }
    suspend fun verifyBrowser(signIn: AccountSignIn, browserProof: String): AccountProfile {
        val code = browserProof.replace(" ", "").replace("-", "").uppercase()
        need(code.matches(Regex("[0-9A-F]{12}")))
        val result = request("sign-ins/${signIn.id}/verify", mapOf("proof" to signIn.proof, "browserProof" to code))
        need(result["status"] == "confirmAccount")
        return profile(result["account"], true)
    }
    suspend fun confirm(signIn: AccountSignIn, browserProof: String): AccountDeviceSession {
        val code = browserProof.replace(" ", "").replace("-", "").uppercase()
        need(code.matches(Regex("[0-9A-F]{12}")))
        val result = request("sign-ins/${signIn.id}/confirm", mapOf("proof" to signIn.proof, "requestId" to signIn.confirmation, "browserProof" to code))
        need(result["status"] == "complete")
        return device(result["session"])
    }
    suspend fun cancel(signIn: AccountSignIn) { request("sign-ins/${signIn.id}/cancel", mapOf("proof" to signIn.proof)) }
    suspend fun refresh(session: AccountDeviceSession): AccountDeviceSession {
        val body = mapOf("refreshToken" to session.refresh, "requestId" to secret())
        return try { device(request("refresh", body)["session"]) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: java.io.IOException) {
            if (error is RoomProtocolException) throw error
            device(request("refresh", body)["session"])
        }
    }
    suspend fun logout(session: AccountDeviceSession) { request("logout", bearer = session.refresh) }
    override fun close() = synchronized(gate) {
        closed = true; pending.forEach { it.cancel(true) }; client.shutdownNow()
    }
    private suspend fun request(path: String, body: Map<String, Any?>? = null, bearer: String? = null): Map<String, Any?> {
        currentCoroutineContext().ensureActive()
        val builder = HttpRequest.newBuilder(address.endpoint("v2/account/$path")).timeout(Duration.ofSeconds(20)).header("Accept", "application/json")
        bearer?.let { builder.header("Authorization", "Bearer $it") }
        val bytes = body?.let { RoomWire.encode(it + ("v" to 2L)).also { builder.header("Content-Type", "application/json") } }
        val request = builder.POST(bytes?.let(HttpRequest.BodyPublishers::ofByteArray) ?: HttpRequest.BodyPublishers.noBody()).build()
        val future = synchronized(gate) {
            if (closed) throw RoomProtocolException("closed")
            client.sendAsync(request) { info -> RoomBodySubscriber(info.headers().firstValueAsLong("Content-Length").orElse(-1)) }.also { pending.add(it) }
        }
        try {
            val response = future.awaitAccount()
            currentCoroutineContext().ensureActive()
            need(response.uri() == request.uri() && response.headers().firstValue("Content-Type").orElse("").substringBefore(';').trim() == "application/json" && response.body().size <= 16384)
            val envelope = RoomWire.parse(response.body())
            need(envelope["v"] == 2L)
            if (response.statusCode() !in 200..299) throw RoomProtocolException("accountUnavailable")
            return RoomWire.obj(envelope["result"])
        } finally { pending.remove(future) }
    }
    companion object {
        internal fun secret(): String = base64(ByteArray(32).also { SecureRandom().nextBytes(it) })
        private fun base64(bytes: ByteArray) = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        private fun need(value: Boolean) { if (!value) throw RoomProtocolException("malformed") }
        private fun token(value: Any?): String = RoomWire.string(value).also { need(it.matches(Regex("[A-Za-z0-9_-]{43}"))) }
        private fun profile(value: Any?, proposal: Boolean = false): AccountProfile {
            val obj = RoomWire.obj(value); val id = RoomWire.string(obj["id"])
            val name = RoomWire.string(obj["name"]); val email = RoomWire.string(obj["email"])
            need((if (proposal) id.isEmpty() else id.matches(Regex("[A-Za-z0-9_-]{43}"))) && name.length in 1..128 && email.length in 1..320 && (name + email).none { it.isISOControl() })
            return AccountProfile(id, name, email)
        }
        private fun device(value: Any?): AccountDeviceSession {
            val obj = RoomWire.obj(value); need(obj["expiresIn"] == 600L)
            return AccountDeviceSession(profile(obj["account"]), token(obj["accessToken"]), token(obj["refreshToken"]))
        }
    }
}
private suspend fun <T> CompletableFuture<T>.awaitAccount(): T = suspendCancellableCoroutine { continuation ->
    whenComplete { value, error ->
        if (error == null) continuation.resume(value)
        else continuation.resumeWithException(if (error is java.util.concurrent.CompletionException) error.cause ?: error else error)
    }
    continuation.invokeOnCancellation { cancel(true) }
}
