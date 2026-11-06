# Meet authorization service

Meet permission extends the authenticated account/room service. It is separate from Google sign-in and from ordered watch commands. Authorization and room-call endpoints have native ports and Library/Player entry on all three desktops. Real Google calling acceptance remains in development. A successful authorization is not a joined call.

Only the current room owner requests `https://www.googleapis.com/auth/meetings.space.created`. Guests need no organizer permission. Ordinary Google sign-in retains `openid`, `profile` and `email`. The backend uses the existing registered confidential callback and verifies Google's subject against the signed-in SyncWatch account; matching email alone is insufficient.

Requests use the [account service](account-service.md) origin, body limits, strict v2 JSON envelopes and access bearer. The start endpoint also requires `X-SyncWatch-Member` and `X-SyncWatch-Run` for the owner's current membership. Transaction endpoints bind the native verifier to that account and device session. Refreshing the device credential preserves this binding; another device of the same account cannot consume it.

| Request | JSON fields in addition to `v: 2` | Result |
| --- | --- | --- |
| `POST /v2/rooms/{roomId}/call-authorizations` | `challenge` | `transactionId`, same-origin `browserUri`, `expiresIn: 300`, `pollAfterMs: 2000` |
| `POST /v2/call-authorizations/{id}/poll` | `proof` | `status`, nullable `account`, nullable `reason` |
| `POST /v2/call-authorizations/{id}/verify` | `proof`, `browserProof` | Verified account only in `confirmAuthorization` |
| `POST /v2/call-authorizations/{id}/confirm` | `proof`, `browserProof`, stable random `requestId` | `complete` after saving the grant |
| `POST /v2/call-authorizations/{id}/cancel` | `proof` | `cancelled`; an already confirmed grant remains valid |

The browser URI begins a single-use OAuth challenge at `/auth/google/meet-start?id=...`. It requests incremental offline permission with explicit consent and account selection. Google passwords and verification codes are entered only in Google's interface. After the callback, the browser displays a separate 12-character uppercase hexadecimal SyncWatch counterproof. Native polling never returns this code or an unverified account profile. The initiating client verifies the code, shows the matching account, then asks for explicit permission confirmation before saving the server grant.

States are `pending`, `authorizing`, `browserConfirmationRequired`, `confirmAuthorization`, `complete`, `cancelled`, `failed` and `expired`. Only `confirmAuthorization` contains `account`. Wrong-account and missing-scope callbacks fail with `accountMismatch` and `permissionUnavailable`; they do not replace an earlier grant. Three incorrect counterproof attempts fail the transaction. Expiry and cancellation clear uncommitted provider credentials. A repeated confirmation with the same request identifier and counterproof returns its result for thirty seconds. Cancellation remains available after room departure while the device session is active.

Google credentials remain on the server. Refresh grants use a separate Data Protection purpose and exclusive writer under the existing Windows DPAPI-protected key ring. Flushed atomic replacement must succeed before the change becomes visible. Corrupt state, purpose mismatch, locked storage and capacity failures fail closed. Native applications receive no Google access/refresh credential. Signing out of SyncWatch does not revoke a previously accepted global organizer grant or close an external Meet window.

The confidential provider adapter keeps short-lived access tokens in memory, bound to the exact grant version. A delayed refresh cannot restore a forgotten grant or attach a token to a newer authorization. Provider responses are bounded; redirects and noncanonical meeting resources are rejected. Meeting creation requests restricted access and supported browser entry; readback must confirm both before publication. The durable operation journal records dispatch before a mutation. Interrupted creation or conference termination becomes an unknown outcome and is never replayed automatically. These components serve the room call API below; actual joined media remains under the external user agent.

Provider HTTP rejections report only the operation, numeric status and an allowlisted Google ErrorInfo reason, or `unspecified`. Response bodies, messages, resource/account/project identifiers, headers and credentials are excluded. Invalid or oversized diagnostic bodies and unavailable diagnostic sinks preserve the original rejection outcome. These operator diagnostics do not trigger a retry or change the native wire contract.

## Room call API

Call requests use the same current-member/run headers. Only the owner may start or end a call. Pending applicants, former members and other rooms cannot obtain the URI. The server returns it only for `ready`; a guest never receives the organizer's provider credentials or permission proposal.

| Request | JSON fields in addition to `v: 2` | Behavior |
| --- | --- | --- |
| `GET /v2/rooms/{roomId}/call` | No body or query | Read the current call state |
| `POST /v2/rooms/{roomId}/call/start` | Stable random `requestId` | Create once, or inspect/recheck the same operation |
| `POST /v2/rooms/{roomId}/call/end` | `callId`, `generation`, stable random `requestId` | End the current space's active conference once |

The result contains `status`, nullable `callId`, `generation`, nullable `meetUri`, nullable `reason`, `authorizationRequired` and `pollAfterMs`. Idle has no call ID and generation zero; other states identify a positive call generation. Statuses are `idle`, `creating`, `ready`, `creationFailed`, `creationUnknown`, `ending`, `ended`, `endFailed`, `endUnknown` and `unavailable`. Polling defaults to five seconds. Missing organizer permission is reported separately; it leaves the room and movie usable.

Creation and termination run in a bounded service-owned queue. An HTTP disconnect does not enqueue another mutation. Clients retain their scoped operation handle and reuse its request identifier when recovering a lost response. A known space with failed policy readback may be checked again through that original operation; this repeats a read, not space creation. The server rechecks room scope after provider I/O and suppresses publication for a closed or replaced room.

Termination records the observed conference before dispatch. A matching request retry reports its preceding outcome. An ambiguous end is not retried against a later conference. `ended` means the provider acknowledged termination or reported no active conference; it does not prove that a participant's browser window closed or stopped local capture.

The service checks known spaces for closed room ownership every five seconds. Owner departure, device logout, room expiry and absent rooms after restart schedule cleanup without borrowing a former native session. Room checks finish before journal or provider I/O. Queue backpressure defers unclaimed cleanup. A monotonic durable closure marker limits each known call to one automatic cleanup attempt. Provider refusal or an ambiguous response remains in the encrypted journal; neither is silently reported as success or automatically retried. An observed replacement of a previously recorded conference is rejected. The provider has no atomic compare-and-end conference operation, so the read-to-end race remains a provider limitation.

Graceful service shutdown first cancels and joins ordinary workers, records interrupted phases, then attempts bounded parallel cleanup of known spaces within the host shutdown deadline. Already ambiguous mutations are never replayed. Abrupt loss after creation without a known space cannot be automatically repaired. Server cleanup never terminates browser processes or claims local camera/microphone ownership.

Account API rate rejection preserves the 120-request per-minute budget and returns HTTP 429 with the v2 `queueFull` error and a `Retry-After` header. Clients must respect the delay and preserve operation correlation. A rate rejection is not permission to create another operation.

The common request/response schemas and native corpus define the wire shape. Backend checks and native HTTP integration with synthetic provider identities are software qualification; real Google consent, supported-browser/PWA handoff and all six directed desktop media cases remain separate delivery requirements. See [Meet integration](architecture/google-meet.md).

## Native interaction

The common Apache-2.0 Design library owns the Meet surface, localized copy, permission confirmation and end-for-everyone confirmation. Platform adapters supply observations and action capabilities. Each application's call controller binds to the current account, room owner, member and epoch independently of movie playback. Losing that source immediately hides the profile and disables obsolete actions; dismiss, leave and shutdown cancel and join owned work.

The organizer explicitly opens authorization, verifies the browser counterproof and confirms the matching profile. Guests only open the ready room call. Before requesting the registered HTTPS handler, every client re-reads the call and requires the same ready call identity, generation and URI. Handoff is explicit and single-flight; an OS acknowledgement is not a joined call. Read recovery never automatically repeats a provider mutation. The organizer must separately confirm termination for everyone. Closing the Meet panel leaves the room, movie and external browser under their respective owners.

An expired permission proposal clears its profile and counterproof, shows a localized expiry notice and offers a fresh authorization request. Old confirmation actions remain disabled. Expiry does not grant access, create a call, leave the room or stop movie playback.
