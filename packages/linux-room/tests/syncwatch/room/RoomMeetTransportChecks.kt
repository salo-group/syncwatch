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
    val reuse = host.beginMeetAuthorization(owner)
    checkNotNull(browser(reuse, "reuse"))
    repeat(2) { check(host.cancelMeetAuthorization(reuse, owner).state == "cancelled") }
    println("SYNCWATCH_MEET_AUTHORIZATION_TRANSPORT_OK synthetic provider, OAuth scope/PKCE, counterproof, owner scope, retry and cancellation")
    return host.beginMeetAuthorization(owner)
}
