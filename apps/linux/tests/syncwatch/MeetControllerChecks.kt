package syncwatch

import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import kotlinx.coroutines.*
import syncwatch.room.*

internal suspend fun meetApplicationChecks(scope: CoroutineScope, host: RoomController, peer: RoomController, service: URI,
    fixtureToken: String, account: AccountProfile, setAccountAvailable: (Boolean) -> Unit) {
    var authorization: URI? = null
    var opens = 0
    var finished = false
    val entered = CompletableDeferred<Unit>()
    val release = CompletableDeferred<Boolean>()
    val owner = MeetController(scope, host) { uri ->
        if (uri.authority == service.authority && uri.path == "/auth/google/meet-start") { authorization = uri; true }
        else { check(uri.host == "meet.google.com"); opens++; entered.complete(Unit); withContext(NonCancellable) { release.await() } }
    }
    val guest = MeetController(scope, peer) { error("Guest browser must remain explicit") }
    suspend fun settles(stage: String, condition: () -> Boolean) {
        try { withTimeout(25_000) { while (!condition()) delay(20) } }
        catch (_: TimeoutCancellationException) {
            error("Meet stage $stage timed out: owner=${owner.presentation.status}/${owner.presentation.authorizationState} actions=${owner.presentation.enabledActions}; guest=${guest.presentation.status}")
        }
    }
    try {
        owner.present(); guest.present()
        settles("initial") { "meetAuthorize" in owner.presentation.enabledActions && guest.presentation.status == "meetIdle" }
        check(guest.presentation.enabledActions.intersect(setOf("meetAuthorize", "meetStart", "meetEnd")).isEmpty())
        owner.action("meetAuthorize"); settles("expiring-authorization-created") { "meetOpenAuthorization" in owner.presentation.enabledActions }
        owner.action("meetOpenAuthorization"); settles("expiring-browser-opened") { authorization != null }
        val expiredCode = completeMeetBrowser(service, checkNotNull(authorization), fixtureToken, "expire")
        settles("permission-expired") { owner.presentation.status == "meetPermissionExpired" && "meetAuthorize" in owner.presentation.enabledActions }
        check(host.connected && peer.connected && opens == 0 && owner.presentation.name.isEmpty() && owner.presentation.email.isEmpty())
        owner.action("editMeetBrowserProof", expiredCode); owner.action("meetConfirm")
        check(owner.presentation.status == "meetPermissionExpired" && owner.presentation.browserProof.isEmpty())
        authorization = null
        owner.action("meetAuthorize"); settles("authorization-created") { "meetOpenAuthorization" in owner.presentation.enabledActions }
        check(owner.presentation.name.isEmpty() && owner.presentation.email.isEmpty())
        owner.action("meetOpenAuthorization"); settles("browser-opened") { authorization != null }
        val code = completeMeetBrowser(service, checkNotNull(authorization), fixtureToken, "denyNextCreate")
        settles("browser-proof-ready") { owner.presentation.authorizationState == "browserConfirmationRequired" }
        check(owner.presentation.name.isEmpty())
        owner.action("editMeetBrowserProof", code); owner.action("meetVerify")
        settles("profile-confirmed") { owner.presentation.authorizationState == "confirmAuthorization" }
        check(owner.presentation.name == account.name && owner.presentation.email == account.email)
        setAccountAvailable(false)
        check(owner.presentation.name.isEmpty() && owner.presentation.email.isEmpty() && "meetConfirm" !in owner.presentation.enabledActions)
        setAccountAvailable(true)
        owner.action("meetConfirm"); settles("permission-granted") { "meetStart" in owner.presentation.enabledActions }
        check(owner.presentation.name.isEmpty())
        owner.action("meetStart"); settles("provider-permission-rejected") { owner.presentation.status == "meetPermissionUnavailable" && "meetAuthorize" in owner.presentation.enabledActions }
        check(host.connected && peer.connected && "meetStart" !in owner.presentation.enabledActions && "meetAuthorize" !in guest.presentation.enabledActions)
        authorization = null
        owner.action("meetAuthorize"); settles("reauthorization-created") { "meetOpenAuthorization" in owner.presentation.enabledActions }
        owner.action("meetOpenAuthorization"); settles("reauthorization-opened") { authorization != null }
        val renewedCode = completeMeetBrowser(service, checkNotNull(authorization), fixtureToken)
        settles("reauthorization-proof") { owner.presentation.authorizationState == "browserConfirmationRequired" }
        owner.action("editMeetBrowserProof", renewedCode); owner.action("meetVerify")
        settles("reauthorization-profile") { owner.presentation.authorizationState == "confirmAuthorization" }
        check(owner.presentation.name == account.name && owner.presentation.email == account.email)
        owner.action("meetConfirm"); settles("reauthorization-granted") { "meetStart" in owner.presentation.enabledActions }
        owner.action("meetStart"); settles("first-call-ready") { "meetOpen" in owner.presentation.enabledActions && "meetOpen" in guest.presentation.enabledActions }
        check("meetEnd" !in guest.presentation.enabledActions)
        owner.action("meetEnd"); check("meetConfirmEnd" in owner.presentation.enabledActions)
        owner.action("meetCancelEnd"); check("meetEnd" in owner.presentation.enabledActions)
        check(checkNotNull(host.callMembership()).owner.callStatus().state == "ready")
        owner.action("meetEnd"); owner.action("meetConfirmEnd")
        settles("first-call-ended") { owner.presentation.status == "meetEnded" && guest.presentation.status == "meetEnded" }
        check(host.connected && peer.connected)
        owner.action("meetStart"); settles("second-call-ready") { "meetOpen" in owner.presentation.enabledActions }
        owner.action("meetRetry"); settles("refreshed-ready") { "meetOpen" in owner.presentation.enabledActions }
        val member = checkNotNull(host.callMembership())
        val beforeRemoteEnd = member.owner.callStatus()
        val remoteEnd = member.owner.callEndOperation(beforeRemoteEnd)
        var remotelyEnded = member.owner.requestCall(remoteEnd)
        withTimeout(5_000) { while (remotelyEnded.state == "ending") { delay(100); remotelyEnded = member.owner.callStatus() } }
        check(remotelyEnded.state == "ended" && "meetOpen" in owner.presentation.enabledActions)
        owner.action("meetOpen"); settles("stale-open-blocked") { owner.presentation.status == "meetEnded" }; check(opens == 0)
        owner.action("meetStart"); settles("third-call-ready") { "meetOpen" in owner.presentation.enabledActions }
        owner.action("meetOpen"); owner.action("meetOpen")
        withTimeout(5_000) { entered.await() }; check(opens == 1)
        setAccountAvailable(false)
        check(owner.presentation.name.isEmpty() && "meetOpen" !in owner.presentation.enabledActions && "meetConfirm" !in guest.presentation.enabledActions)
        owner.membershipChanged(); guest.membershipChanged()
        val close = scope.launch { owner.close(); finished = true }
        yield(); check(!finished)
        release.complete(true); close.join()
        check(owner.presentation.status != "meetHandedOff" && opens == 1)
        println("SYNCWATCH_MEET_APPLICATION_OK synthetic provider, profile fence, owner/guest, end confirmation, explicit single-flight handoff and late scope loss")
    } finally {
        release.complete(true)
        withContext(NonCancellable) { owner.close(); guest.close() }
    }
}
private suspend fun completeMeetBrowser(service: URI, authorization: URI, fixtureToken: String, scenario: String = "accept"): String {
    check(authorization.rawQuery.startsWith("id="))
    val bytes = RoomWire.encode(mapOf("v" to 2L, "transactionId" to authorization.rawQuery.removePrefix("id="), "scenario" to scenario, "fixtureToken" to fixtureToken))
    val request = HttpRequest.newBuilder(service.resolve("/__test/meet-browser")).timeout(Duration.ofSeconds(5))
        .header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofByteArray(bytes)).build()
    val body = withContext(Dispatchers.IO) {
        HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(5)).build().use { client ->
            val response = client.send(request, HttpResponse.BodyHandlers.ofByteArray())
            check(response.statusCode() == 200 && response.body().size <= 4096)
            response.body()
        }
    }
    return RoomWire.string(RoomWire.obj(RoomWire.parse(body)["result"])["browserProof"])
}
