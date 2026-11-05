# Meet authorization service

Meet permission extends the authenticated account/room service. It is separate from Google sign-in and from ordered watch commands. These endpoints are implemented; native permission dialogs, meeting provisioning endpoints and real calling acceptance remain in development. A successful authorization is not a joined call.

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

Google credentials remain on the server. Refresh grants use a separate Data Protection purpose and exclusive writer under the existing Windows DPAPI-protected key ring. Flushed atomic replacement must succeed before the change becomes visible. Corrupt state, purpose mismatch, locked storage and capacity failures fail closed. Native applications receive no Google access/refresh credential. Signing out of SyncWatch does not revoke a previously accepted global organizer grant or stop an external browser call.

The confidential provider adapter keeps short-lived access tokens in memory, bound to the exact grant version. A delayed refresh cannot restore a forgotten grant or attach a token to a newer authorization. Provider responses are bounded; redirects and noncanonical meeting resources are rejected. Meeting creation requests restricted access and supported browser entry; readback must confirm both before publication. The durable operation journal records dispatch before a mutation. Interrupted creation or conference termination becomes an unknown outcome and is never replayed automatically. These provider/journal components do not yet expose a complete room-to-call workflow.

The common request/response schemas and native corpus define the wire shape. Backend checks and native HTTP integration with synthetic provider identities are software qualification; real Google consent, supported-browser/PWA handoff and all six directed desktop media cases remain separate delivery requirements. See [Meet integration](architecture/google-meet.md).
