# Syncplay helper and native pipes

The development helper embeds the source-built CPython/Syncplay/TLS runtime in a dedicated process. The application owns that process and its inherited stdin/stdout pipes. It is a provider integration boundary, independent of shared design and window rendering. A controlled player can be attached before the native VLC provider is available.

`packages/ipc` owns strict schema validation, incremental framing and bounded command replay. The contract checker and helper use the same implementation. `packages/syncplay-helper` owns isolated native startup, pipe threads, one reactor/controller and the upstream-engine adapter. Syncplay retains all synchronization decisions. Native transports live in `packages/macos-bridge`, `packages/windows-bridge` and `packages/linux-bridge`; they contain no layout or synchronization timing policy.

The helper requires `bridge.hello` before work, fixes the process run and media generation, correlates results, rejects contradictory command reuse and retains completed results without executing duplicates. Each direction has an independent command sequence. Queues, frames and pending corrections are bounded. A full queue, an invalid stream, a missing acknowledgement or EOF terminates the owned scope. Native process owners enforce a bounded shutdown and terminate a helper that fails to exit. A failed provider must pause/release its native player through the application resource owner.

`player.loaded` supplies an opaque authorized handle and local filename/duration/size metadata. It authorizes no arbitrary path or network URL. `player.state` reports actual native observations. Its optional `origin` is `observation` by default; only explicit `user` observations may authorize the upstream user-change/send path. Other message families cannot claim that origin. Each user-origin observation carries a strictly increasing `actionSequence`. Corrections carry `afterUserAction`: the native owner rejects a correction older than its latest actual user action before mutating the player, then returns correlated `cancelled`. The helper accepts that cancellation only when the request is demonstrably superseded; other provider failures close the scope. Engine corrections are sent as `player.setPaused`, `player.setPosition`, `player.setSpeed` or `player.openFile`; a result is correlated with its correction and supplies actual pause/position with observation authority only. The helper keeps ping heartbeats but omits stale playstate while ordered native corrections await acknowledgement. Expected command values never become observations. Acknowledging connection initiation does not assert an authenticated connection. Readiness requires the initial canonical state and an observed ready player.

Media selection belongs to the application coordinator. The helper disables the stock shared-playlist controller through the pinned engine configuration so it cannot independently reopen or rewind an application-owned file. This does not disable upstream play/pause/seek, readiness, drift correction or transport recovery. Shared movie/playlist-item selection still requires the application selection and RoomMetadata integration.

The native `PlayerCorrection` boundary decodes the four player command kinds on Swift, C# and Kotlin. It rejects malformed framing, duplicate/unknown fields, invalid scalar types and ranges, and foreign wire scopes before reaching a native player. Its trusted native scope remains separate from the helper run so a restarted helper cannot take ownership of another player. The selection owner authorizes an opaque handle; an `openFile` request for the already loaded handle observes that file without reopening or rewinding it. Other handles fail closed.

A correction submission returns an actual `PlaybackObservation`. Only that observation supplies the correlated result's pause and position; the requested target never substitutes for native state. The coordinator must check its action fence before execution and after the native await, and publish a superseding actual user frame before sending a cancelled reply. This command boundary does not itself launch or connect the helper, decide drift, or establish room/media agreement. Native `HelperCommands` encodes the existing handshake, connection, media, readiness and shutdown messages. It validates metadata and endpoint bounds, fixes TLS, and serializes native state from an observation checked against the trusted native owner. Ordinary observations always have observation authority; a separate explicit method requires a valid user action sequence. Local volume, mute, track and presentation fields never enter these frames. The session owner remains responsible for serialized writes, sequence allocation and publication acknowledgement. `HelperReply` verifies each acknowledgement against its pending command, private wire scope and expected response kind; a handshake also matches the trusted Syncplay revision. Unknown or duplicate fields, malformed scalar values and contradictory acknowledgements fail closed. Structured provider rejection retains its code and retryability without retaining private provider text.

The current helper accepts public trusted TLS endpoints. `secretHandle` fails as unavailable until the application credential broker is attached. Standard output contains protocol frames only. Upstream console text is suppressed; native adapters discard diagnostics without accumulating them.

## Local package verification

Each native bridge exposes an immutable `HelperRuntime` obtained through verification. It checks the development receipt, native target, pinned Syncplay revision, complete file inventory and streaming SHA-256 digests before returning an executable path. Unexpected, missing or changed files, links and noncanonical inventory paths fail verification. The revision file must agree with the application policy. Verification supports cancellation and closes its file handles; it does not launch a helper.

`tools/runtime-policy/generate.py` derives the native runtime policy from the registered `3rdparty/syncplay` Gitlink. Repository validation checks the generated Swift, C# and Kotlin adapters, so platform-specific runtime pins cannot drift independently. The policy belongs to the provider boundary and contains no Design values.

Application-local configuration defines the package location: `Contents/Frameworks/Syncplay` in a macOS bundle, `Syncplay` beside the Windows executable, and `Syncplay` beside the Linux application classes or JAR. The configuration types also parse an explicit development package override. Room metadata cannot supply paths. The normal shared-watch coordinator verifies this package before transferring the selected native player to its helper session on every client.

These are development integrity receipts, not release signatures or atomic filesystem seals. Instrumented packages with unlisted test artifacts are rejected. Distribution authentication, installed-package ownership and complete normal Room Start acceptance remain release requirements; successful package verification or pipe handshakes alone do not establish readiness.

Room-bound sessions can require the exact declared Syncplay username. In that mode a renamed identity fails before connection or readiness is accepted; failure pauses the borrowed native player without disposing it. Standalone development sessions retain the upstream naming behavior. Native session tests cover both policies.

Shared load requests carry their admission requirement into the native controller at construction. Play, seek and skip remain withheld before an admitted connection and readiness, including the media-preparation interval before a helper exists. This also requires the declared identity when attaching the helper. Normal workspace Start remains gated until the complete room, media and helper coordinator is integrated and qualified on all three platforms.

`HelperSession` on Swift, C# and Kotlin owns one helper transport and borrows one already loaded native player. Its private wire scope is independent of the native media scope. Construction requires admitted media metadata, a validated TLS endpoint and the trusted engine revision. Media agreement and room admission belong to the application coordinator. Starting a session pauses the player, performs the correlated handshake and publishes actual native observations. `sync.identity` reports the authenticated server-accepted username before the connected event; callers must use that identity when binding room metadata. The connection acknowledgement alone never establishes identity or readiness.

Each session has one bounded reader and one serialized writer/operation owner. Explicit UI intents enter synchronously on the owning UI context. An incoming correction may queue while a user observation write is in flight; publication advances only after that write succeeds. Superseded corrections receive cancellation only after the newer actual observation is sent. Completed corrections and replies have bounded replay caches, and contradictory reuse fails closed. The helper likewise accepts identical completed native results without applying them again.

Syncplay readiness is participant state, not permission to use playback controls: an explicit pause can legitimately clear the initiating participant's readiness. Authenticated connected sessions continue accepting explicit actions in that state. The adapter selects Syncplay's upstream `UNPAUSE_ALWAYS_MODE` for the application's direct play control, rather than combining play with the stock client's readiness toggle. Syncplay still owns canonical state, timing and corrections; the native application never invents a local synchronization policy.

Missing replies, malformed input, queue exhaustion and helper EOF stop the session. Cleanup pauses the borrowed player through an independent cancellation context and closes the owned transport. Await session close before replacing it or releasing the native player. Session close is repeatable and does not close the borrowed player. These lifecycle and ordering checks run in all three native language lanes; they do not establish room/media agreement or physical cross-platform acceptance.

Build and exercise a previously source-built engine payload:

```sh
python3 tools/bridge/check.py --runtime artifacts/dependencies/syncplay-runtime/macos-arm64/payload --output artifacts/helper/macos-arm64
python3 -m unittest discover -s tools/bridge/tests -v
swift run --package-path packages/macos-bridge BridgeProbe /absolute/helper/bin/syncwatch-helper /absolute/helper
dotnet run --project packages/windows-bridge/SyncWatch.Bridge.Probe -- /absolute/helper/bin/syncwatch-helper /absolute/helper
# Linux only; requires provisioned JDK/Kotlin and the source-built kotlin-stdlib artifact:
python3 tools/bridge/linux_probe.py /absolute/helper/bin/syncwatch-helper /absolute/helper
```

Run Swift only on macOS, C# only on Windows and Kotlin only on Linux. Use the corresponding target directory and `.exe` name on Windows. The dependency action calls `tools/bridge/ci.py`, which follows the same native dispatch. Linux provisioning selects the JDK and Kotlin compiler and builds the owned stdlib through its registered dependency recipe; the probe verifies that artifact before compiling. `--kotlin-runtime` selects an existing `kotlin-stdlib/linux-x64` artifact directory for reuse in both provisioning and checking. Each process probe covers handshake, identical replay, loaded media, shutdown, cancellation and an owned helper crash. Kotlin probe classes are temporary and every owned helper is closed and checked absent, including failure paths.

Build inputs come from the existing runtime receipt and pinned fastjsonschema Git submodule. No package installation or application dependency download occurs inside the helper builder. Its receipt retains every payload digest and original validator grant.

This is a development helper. Authenticated immutable installation/signing, physical media/device acceptance and normal room admission remain separate requirements. Compiled Kotlin command and application consumers exercise the owned source-built JVM/UI payload. Packaged application/runtime qualification and visible physical/provider acceptance remain separate. Process probes do not establish media agreement, room admission or six-direction playback acceptance.

## Account transport admission

Matching native adapters and helper packages extend v1 with an explicit capability. `bridge.ready` advertises `admissionVersion: 1`. A native account session requires that value before sending `sync.connect` with `accountAdmission: true`; legacy validation omits the flag. Missing support fails closed rather than opening a stock listener or transferring an account credential.

After verifying the server certificate, each transport requests a fresh nonce through `sync.admissionRequired`. The native owner rereads the current watch generation and media agreement, obtains a one-use room ticket, rechecks scope, and returns `sync.admit` with that nonce, ticket and the room member's actual client-run ID. The helper sends the ticket only over the verified TLS channel and waits for the bound admission acknowledgement before upstream `Hello`. The private IPC run remains distinct from the room run. Google and SyncWatch account access/refresh tokens never enter the helper.

Native writers queue the challenge until the current command acknowledgement completes; an event arriving before the connect acknowledgement cannot create nested writes. A bounded credential wait is canceled and joined on close. Noncanonical or replayed nonces, stale rooms/generations, unavailable account authority and late callbacks fail without sending a credential to a replacement transport. Reconnection requests another ticket; no credential is reused or persisted. The typed admission DTO has redacted diagnostic descriptions.

Credential preparation uses the authenticated transport's ten-second admission window.
Metadata event recovery can outlast an ordinary IPC acknowledgement while the established membership remains valid.
Replacement admission reads the current frozen selection through the retained membership owner.
That read reauthorizes the account and member, checks the original active generation, and leaves catalog cursors unchanged.
It validates the media before requesting a new ticket.
It repeats that read and validation after issuance, before transferring the ticket to the helper.
An established scope can retry transport errors while its event stream reconnects.
The admission read uses no event or command gate and changes no counters or sequence floors.
Its original bootstrap remains an immutable anchor; it is never published as a new snapshot.
New starts still require connected metadata.
Account loss, member replacement, changed generation, and media disagreement remain terminal.
The native bridge must not end that credential wait after five seconds.
Ordinary IPC acknowledgements retain their five-second limit.
The overall recovery budget remains 25 seconds, and expired transport challenges remain terminal.

An already admitted native transport can retain its frozen media under the existing continuation policy.
Each replacement transport requires fresh server metadata and the original active generation, independently of continuation permission.
The enforcing server accepts both empty upstream list forms (`null` and `{}`), keeps room isolation, and rejects nonempty filters and invalid types.

Shared native command/reply fixtures and scripted peers verify correlation, capability, fresh reconnect authority and canceled credential waits. Real TLS checks run the pinned client against the private enforcing server, including revocation and shutdown. These checks use synthetic authority and a simulated player; they do not qualify packaged VLC windows, Google sign-in, or cross-desktop watch acceptance.
