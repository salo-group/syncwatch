# Authenticated room service

The optional v2 authority connects Google-backed SyncWatch device sessions, private room metadata and one owned TLS Syncplay listener. Local media stays on each desktop. Desktop admission adapters and the complete room-to-Meet journey are still being integrated; the API alone does not qualify those workflows.

## Deployment

Configure the [account module](architecture/accounts-and-rooms.md) and these server-owned settings together:

| Setting | Meaning |
| --- | --- |
| `SYNCWATCH_SYNCPLAY_SERVER_RUNTIME` | Absolute directory of the matching source-built admission helper package |
| `SYNCWATCH_SYNCPLAY_TLS_DIRECTORY` | Absolute directory containing the listener's `privkey.pem`, `cert.pem` and `chain.pem` |
| `SYNCWATCH_SYNCPLAY_BIND_ADDRESS` | Listener IP address |
| `SYNCWATCH_SYNCPLAY_PUBLIC_HOST` | Certificate-valid host advertised to admitted clients |
| `SYNCWATCH_SYNCPLAY_PORT` | Listener port, 1–65535 |

Partial configuration fails at startup. The listener must complete its private authority handshake before HTTP startup succeeds. Invalid certificates or an unavailable helper fail closed. Public deployment requires HTTPS for the account/room API and a trusted certificate for the advertised Syncplay host. The current account key protection deployment is Windows DPAPI; this does not change the required desktop languages or operating systems.

In this mode, `/v1/rooms` routes are absent and a legacy creation key is unnecessary. There is no stock Syncplay listener or anonymous-room fallback. Without authority configuration, the separate legacy service retains its existing behavior.

A lost helper/control channel stops the HTTP application with a failing exit status. Shutdown closes and joins the owned helper and its connections. Rooms and admission tickets are ephemeral; service restart does not preserve membership or permit old-ticket replay.

## Authentication and wire contracts

Use the current SyncWatch access token in `Authorization: Bearer`; never send a Google credential, refresh token, membership token or invitation as a query parameter. Member operations additionally require exactly one `X-SyncWatch-Member` and `X-SyncWatch-Run` header. Authorization binds the current device session, member, room epoch and process run. Another device signed into the same Google account receives no owner or member bypass.

Control bodies use the exact [v2 request schema](../contracts/v2/room-access-request.schema.json). HTTP results and errors use the [v2 response envelope](../contracts/v2/room-access-response.schema.json). Creation accepts only `v`, `clientRunId` and `requestId`; the service chooses the Syncplay destination, room and username. An admission contains `roomId`, `memberId`, `expiresAt` and `bootstrap`, with no legacy membership credential.

The nested ordered watch protocol remains v1: bootstrap, snapshots, catalogs, selections, commands and WebSocket event frames preserve their existing ordering and generation rules. Versioning the access authority does not grant access through legacy credentials. All HTTP bodies and frames remain bounded to 64 KiB; unknown fields and duplicate keys are rejected. Browser-origin access is disabled.

## Admission and member operations

| Route | Permission and input |
| --- | --- |
| `POST /v2/rooms` | Account; `v`, `clientRunId`, correlated `requestId` |
| `POST /v2/rooms/{room}/invitation` | Owner; empty body; rotate the single-use invitation |
| `POST /v2/rooms/{room}/admission-requests` | Account; `v`, `clientRunId`, correlated `requestId`, `invitation` |
| `GET /v2/room-admissions/{requestId}` | Requesting device and run; its original client request ID |
| `POST /v2/room-admissions/{requestId}/accept` | Approved requesting device; `v`, `clientRunId` |
| `POST /v2/room-admissions/{requestId}/cancel` | Requesting device; `v`, `clientRunId`; withdraw or release a raced admission |
| `GET /v2/rooms/{room}/pending` | Owner; pending requests with provider-derived account ID, name and email |
| `POST /v2/rooms/{room}/decision` | Owner; `v`, opaque `approvalId`, boolean `approve` |
| `GET /v2/rooms/{room}/snapshot` | Member; bootstrap |
| `GET /v2/rooms/{room}/catalog?cursor=...` | Member; existing bounded catalog page |
| `GET /v2/rooms/{room}/selection?watchGeneration=N` | Member; existing selection definition |
| `POST /v2/rooms/{room}/commands` | Member; existing v1 ordered command |
| `GET /v2/rooms/{room}/events` | Member; existing WebSocket stream, optional `epoch` and `cursor` |
| `POST /v2/rooms/{room}/sync-ticket` | Member; empty body; a fresh transport-specific admission ticket |
| `POST /v2/rooms/{room}/revoke` | Owner; `v`, target `memberId` |
| `POST /v2/rooms/{room}/leave` | Member; empty body; owner departure closes the room |

The requesting client retains its own `requestId` for status and acceptance. The owner uses the distinct server-generated request `id` as `approvalId`. Requests expire after two minutes. Display identity comes from the account repository; a requester cannot submit another person's name or email. Pending or denied clients receive no catalog, Syncplay ticket or meeting URI.

Cancel by that original client request ID when an attempt ends before its accept outcome is known. Cancellation is idempotent while the correlated request is retained: it withdraws a pending/approved request or removes its already-admitted guest and revokes that member's synchronization authority. It never removes the owner or another device/run. Known members normally use `leave`; request records are retained only for the bounded admission/recovery window.

## Desktop account authority

The native room SDKs borrow a `RoomAccountAuthority` from their account client after the device-session checkpoint succeeds. This in-memory authority binds the service origin, account-client lifetime and account identity. Pause it before refresh rotation, publish the replacement only after its secure checkpoint, and mark it unavailable if rotation or storage fails. Closing a room cancels and joins its own HTTP, event connection and credential waits; it does not close the borrowed account authority.

An authenticated admission carries no legacy membership credential. Its member, process run and authority scope must match before a native adapter sends a request. Legacy clients reject authenticated admissions, and account clients reject legacy create/join operations. The service supplies the synchronization destination and username. The SDK preserves existing ordered content models while unwrapping strict v2 HTTP envelopes. Redirects, cookies, arbitrary response errors and credential-bearing query strings remain disabled.

If HTTP returns401 during rotation, a native adapter may repeat the same correlated request once only when the same authority supplies a different access token. It never repeats an unchanged token or switches service/account to recover. Every synchronization transport obtains a new short-lived ticket through the account room port. These adapters do not establish native UI, media synchronization or calling acceptance on their own.

Each ticket is single-use, short-lived, and bound to the exact room, epoch, device, run, username and TLS destination. Every new transport needs a fresh ticket. Live leases are rechecked against account and room authority; logout, leave, revocation and expiry close metadata and synchronization connections. Account-token rotation retains the device authority, while old bearer tokens cease authorizing new HTTP requests.

## Calling boundary

Arbitrary `call.linkChanged` commands are forbidden in this mode. Organizer authorization, durable Meet provisioning outcomes, restricted space configuration and supported browser/PWA participation belong to the call module. API success or a room membership never establishes joined media, camera/microphone capture or call termination.
