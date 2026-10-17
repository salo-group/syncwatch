# Syncplay and room metadata

## Engine integration

Each device runs the pinned upstream `SyncplayClient` in a headless Python helper. The helper receives application-specific `BridgePlayer` and `BridgeUI` adapters, connects to a standard Syncplay server, and controls the native VLC adapter through local IPC.

The integration relies on a pinned internal Python API rather than a separately published stable SDK. Compatibility must be checked before every dependency update.

| Upstream entry point in `3rdparty/syncplay` | Integration |
|---|---|
| `syncplay/client.py:SyncplayClient.__init__(playerClass, ui, config)` | Inject the player and noninteractive UI adapters |
| `syncplay/players/basePlayer.py:BasePlayer` | Implement status requests, pause, position, speed, file opening, messages, startup, and cleanup |
| `SyncplayClient.updatePlayerStatus(paused, position)` | Report actual native playback state in seconds |
| `syncplay/protocols.py` | Reuse upstream transport, room, readiness, and playlist behavior |
| `syncplay/ui/consoleUI.py` and `UiManager` in `client.py` | Identify the required noninteractive UI callbacks |

The helper does not launch the standard Syncplay UI or a separate VLC application. A custom BasePlayer adapter controls embedded libVLC directly. Drift correction remains in the upstream engine.

The first executable integration must verify headless imports, complete configuration from upstream defaults, adapter callbacks, and lifecycle without a GUI dependency. Package Python and its complete transitive source graph under the dependency policy before distributing the helper.

## Playback flow

```mermaid
sequenceDiagram
    participant UI as Native UI
    participant VLC as Native VLC adapter
    participant B as Local Syncplay bridge
    participant S as Syncplay server
    participant P as Peer bridge and player
    UI->>VLC: Local play, pause, or seek action
    VLC->>B: Actual state and command origin
    B->>S: Upstream Syncplay update
    S->>P: Canonical shared state
    S->>B: Canonical shared state
    B->>VLC: Pause, position, or speed correction
    VLC->>B: Actual result with acknowledged origin
```

A single command queue owns the native player. The coordinator applies local user actions and reports actual state to the engine. An engine correction must not become a second user command. Track origin, command ID, and media generation for ordering and acknowledgement while leaving drift decisions to Syncplay.

The application boundary uses integer milliseconds; upstream Python APIs use floating-point seconds. Obtain positions from VLC. Use a monotonic clock for elapsed time rather than treating another device's wall-clock timestamps as the playback clock.

## Local IPC v1

Use inherited anonymous pipes for `stdin` and `stdout` on both platforms. Frames are newline-delimited UTF-8 JSON. Standard output contains protocol frames only; standard error carries sanitized diagnostics. Serialize writes and read asynchronously away from the UI thread.

Example engine-to-player request:

```json
{"v":1,"id":"cmd-42","type":"player.setPosition","clientRunId":"run-7","mediaGeneration":3,"payload":{"positionMs":140500}}
```

Example acknowledgement:

```json
{"v":1,"replyTo":"cmd-42","type":"player.result","clientRunId":"run-7","mediaGeneration":3,"payload":{"status":"applied","positionMs":140500,"paused":true}}
```

| Message family | Responsibility |
|---|---|
| `bridge.hello / ready` | Negotiate IPC version, upstream revision, process run ID, and capabilities before accepting commands |
| `sync.connect / disconnect / setReady` | Manage Syncplay connection and readiness |
| `player.state` | Report actual time, pause, rate, duration, seekability, and loaded generation |
| `player.setPaused / setPosition / setSpeed / openFile` | Execute BridgePlayer actions in the native adapter |
| `sync.connection / participants / readiness` | Publish engine state to the application |
| `bridge.error / shutdown / stopped` | Report failures and coordinate shutdown |

`openFile` resolves an opaque local media handle through the coordinator. Remote descriptors cannot request arbitrary local paths. Network URL playback is outside the initial local-file feature set.

Limit a frame to 64 KiB including LF and bound all queues. Coalesce redundant position observations while retaining commands, acknowledgements, and failures. Use monotonically increasing command IDs and a bounded replay cache: return retained duplicate results without repeating a seek and reject older evicted IDs. Reject unsupported major versions, malformed frames, foreign process run IDs, and stale media generations. [Shared contracts](contracts.md) define the executable schemas, ordering, units, framing, and rejection cases. Exact runtime timeout values accompany the bridge implementation.

The application owns the helper. On EOF, the helper releases its engine and exits. After a bounded graceful shutdown, the parent may terminate only its own child. Use a Windows Job Object and an owned macOS process group with a signed embedded runtime. A helper crash pauses playback without closing the UI. A replacement helper receives a new run ID and current state; obsolete pending seeks are discarded.

## RoomMetadata service

Syncplay owns shared play, pause, seek, rate, and readiness. A separate C# / ASP.NET Core service handles catalog exchange, media identity, watch-session selection, missing-file state, and the Meet URI over HTTPS/WSS. It does not implement a second playback controller or relay movie content.

Proposed endpoints:

| Endpoint | Responsibility |
|---|---|
| `POST /v1/rooms` | Create a room, owner credential, and binding to a Syncplay endpoint and room |
| `POST /v1/rooms/{id}/join` | Exchange an invitation for a scoped room token, snapshot, and member ID |
| `GET /v1/rooms/{id}/events` over WSS | Exchange catalog batches, membership bindings, selections, missing-media state, watch lifecycle metadata, and the Meet URI |
| `POST /v1/rooms/{id}/leave` | Revoke participation and clear ephemeral descriptors |

Each event includes a protocol version, server-issued room epoch and sequence number, member ID, and process run ID. Selection and watch start/end update a server-ordered watch generation. Reject obsolete epochs and generations. Reconnect uses a snapshot and resume cursor; missing events require a new snapshot. Client timestamps do not establish event order.

## Channel binding and lifecycle

A room snapshot defines the Syncplay endpoint, room name, and agreed client identity. Bind the metadata member ID to the username actually accepted by Syncplay after its handshake and to the process run ID. An entered display name or Meet identity alone is not a reliable cross-channel key.

Start flow: agree on media selection and generation, resolve authorized local handles, verify identity and duration, load paused, set Syncplay readiness, and permit upstream playback. A late participant receives the selection snapshot before applying the current playback position.

If metadata connectivity fails during playback, retain the agreed file and continue Syncplay. Operations requiring a new shared selection wait for recovery. Local End always releases resources; a deferred shared End applies only if its generation is still current. If Syncplay fails, pause and become not-ready regardless of the other connections.

## Media identity

Use a normalized logical title ID to find candidates, then compare duration, file size, and available content digests. A title, duration, or resolution alone does not establish an identical edition. Cache full content hashes in the background, invalidating them after a size or modification-time change.

An identical full hash is a strong match. Different encodings or missing hashes require explicit edition confirmation. Ambiguous candidates never trigger automatic playback. Absolute paths remain local. Send catalog deltas in bounded batches over RoomMetadata rather than placing an entire library inside a helper IPC frame.

## Server compatibility

Use the pinned Syncplay server version for controlled integration tests and test interoperability with a standard client separately. Real connections require TLS without silent downgrade. A Syncplay server password is not a substitute for application room authorization; invitations and RoomMetadata authorization are separate concerns. Do not distribute shared server credentials in application binaries.

Validate native-to-native playback, native-to-standard-client playback, latency variation, conflicting actions, missing files, late join, server restart, and helper failure. Standard Syncplay clients are not required to implement SyncWatch-specific catalog metadata.

See the [Syncplay protocol specification](https://syncplay.pl/about/protocol/). The pinned source defines the concrete engine behavior and integration points.
