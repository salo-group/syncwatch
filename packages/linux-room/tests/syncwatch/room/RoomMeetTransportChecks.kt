package syncwatch.room

suspend fun meetTransportChecks(host: RoomClient, peer: RoomClient, owner: RoomAdmission,
    guest: RoomAdmission, account: AccountProfile, fixtureToken: String): RoomMeetAuthorization {
    suspend fun browser(flow: RoomMeetAuthorization, scenario: String): String? {
        val body = RoomWire.encode(mapOf("v" to 2L, "transactionId" to flow.id, "scenario" to scenario, "fixtureToken" to fixtureToken))
        val result = RoomWire.parse(host.request("POST", "__test/meet-browser", body = body))
        RoomWire.keys(result, "browserProof")
        return result["browserProof"]?.let(RoomWire::string)
    }
    try { peer.beginMeetAuthorization(guest); error("Guest requested organizer permission") }
    catch (failure: RoomProtocolException) { check(failure.code == "forbidden") }
    val call = host.callStartOperation(owner)
    val unsignedCall = host.requestCall(call, owner)
    check(unsignedCall.state == "idle" && unsignedCall.authorizationRequired)
    val mismatch = host.beginMeetAuthorization(owner)
    check(host.meetAuthorizationStatus(mismatch, owner).state == "pending")
    check(browser(mismatch, "wrongAccount") == null)
    val failed = host.meetAuthorizationStatus(mismatch, owner)
    check(failed.state == "failed" && failed.reason == "accountMismatch")
    val refused = host.beginMeetAuthorization(owner)
    check(browser(refused, "missingScope") == null)
    check(host.meetAuthorizationStatus(refused, owner).reason == "permissionUnavailable")
    val flow = host.beginMeetAuthorization(owner)
    check(!flow.toString().contains(flow.proof))
    try { peer.meetAuthorizationStatus(flow, guest); error("Foreign flow reached transport") }
    catch (failure: RoomProtocolException) { check(failure.code == "malformed") }
    val code = checkNotNull(browser(flow, "accept"))
    val waiting = host.meetAuthorizationStatus(flow, owner)
    check(waiting.state == "browserConfirmationRequired" && waiting.account == null)
    val wrong = (if (code[0] == 'A') "B" else "A") + code.drop(1)
    val invalid = host.verifyMeetAuthorization(flow, owner, wrong)
    check(invalid.reason == "invalidConfirmation" && invalid.account == null)
    val verified = host.verifyMeetAuthorization(flow, owner, code)
    check(verified.state == "confirmAuthorization" && verified.account == account)
    repeat(2) { check(host.confirmMeetAuthorization(flow, owner, code).state == "complete") }
    check(host.cancelMeetAuthorization(flow, owner).state == "complete")
    host.requestCall(call, owner)
    val ready = kotlinx.coroutines.withTimeout(5_000) {
        var status = host.callStatus(owner)
        while (status.state == "creating") { kotlinx.coroutines.delay(10); status = host.callStatus(owner) }
        status
    }
    check(ready.state == "ready" && ready.meetUri != null)
    val retry = host.requestCall(call, owner)
    check(retry.callId == ready.callId && retry.generation == ready.generation)
    val guestCall = peer.callStatus(guest)
    check(guestCall.meetUri == ready.meetUri && !guestCall.authorizationRequired)
    val forbiddenEnd = peer.callEndOperation(guest, guestCall)
    try { peer.requestCall(forbiddenEnd, guest); error("Guest ended owner call") }
    catch (failure: RoomProtocolException) { check(failure.code == "forbidden") }
    val end = host.callEndOperation(owner, ready)
    host.requestCall(end, owner)
    val ended = kotlinx.coroutines.withTimeout(5_000) {
        var status = host.callStatus(owner)
        while (status.state == "ending") { kotlinx.coroutines.delay(10); status = host.callStatus(owner) }
        status
    }
    check(ended.state == "ended" && ended.meetUri == null)
    check(host.requestCall(end, owner).state == "ended")
    println("SYNCWATCH_ROOM_CALL_TRANSPORT_OK synthetic provider, current membership, stable create/end, guest denial and URI fencing")
    val reuse = host.beginMeetAuthorization(owner)
    checkNotNull(browser(reuse, "reuse"))
    repeat(2) { check(host.cancelMeetAuthorization(reuse, owner).state == "cancelled") }
    println("SYNCWATCH_MEET_AUTHORIZATION_TRANSPORT_OK synthetic provider, OAuth scope/PKCE, counterproof, owner scope, retry and cancellation")
    return host.beginMeetAuthorization(owner)
}
