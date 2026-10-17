# Shared contracts

The Swift client, C# client, metadata service, and Python helper implement the same versioned wire boundary. Schemas live in [contracts/v1](../../contracts/v1/); [the manifest](../../contracts/manifest.json) lists seven entry points. [Acceptance fixtures](../../contracts/fixtures/acceptance.json) and the [reference validator](../../tools/contracts/check_contracts.py) define structural and contextual acceptance. These artifacts do not implement a player, synchronization engine, service, or call host.

## Encoding, limits, and compatibility

Use JSON Schema Draft 7. Schema identities under `https://schemas.syncwatch.invalid/v1/` are identifiers resolved from the checkout, never network endpoints. Resolve all references locally. The Python checker loads the pinned fastjsonschema source with default insertion disabled. See the [Draft 7 specification](https://json-schema.org/draft-07/draft-handrews-json-schema-validation-01) and [validator API](https://horejsek.github.io/python-fastjsonschema/).

Every entry has `v: 1`. Objects reject unknown fields. Unsupported versions fail before dispatch. A change to accepted wire fields, semantics, or limits requires a new major version and a separate schema directory. Negotiate supported versions before accepting helper commands; do not interpret unknown versions using v1. Corrections to documentation and additional examples may retain the same version when acceptance is unchanged.

| Value | Contract |
|---|---|
| Transport | UTF-8 JSON; reject BOM, invalid Unicode, and unpaired surrogates |
| Helper framing | One JSON object followed by LF; no CR or additional physical LF inside a frame |
| Frame size | At most 65,536 encoded bytes, including the final LF; check before parsing |
| Nesting | At most 32 container/child levels from a root at depth zero |
| IDs | 1-64 ASCII characters; begin with a letter or digit, then letters, digits, `.`, `_`, or `-` |
| Generations, sequence, file size | Nonnegative integers up to 9,007,199,254,740,991; sequence events and file sizes start at one |
| Position and duration | Integer milliseconds, 0-604,800,000 inclusive; unknown duration is `null` |
| Playback rate | Finite multiplier, 0.25-4 inclusive; normal speed is 1 |
| Display text / diagnostics | At most 256 / 512 Unicode scalar values; no ASCII control characters |
| Room / helper membership | At most 64 members per snapshot or participant message |
| Catalog batch | At most 100 descriptors; split larger catalogs using `batchId` and `final` |

Integer fields must use integer JSON tokens, without a fraction or exponent. Booleans are not numbers. Reject duplicate keys at every level, non-finite numbers, overflow, and excessively nested input. String limits count Unicode scalar values, not UTF-16 code units. Escaped newlines inside JSON strings do not delimit frames, but text fields independently forbid control characters.

The helper's bounded stream reader must reject a frame as soon as its byte limit is exceeded, before buffering an unbounded line. The reference `decode_frame` checks complete frames; it is not a streaming reader. Metadata transports apply the same per-message byte and depth limits before decoding. A snapshot's structural member limit does not override the byte limit.

## Entry points

| Schema | Meaning |
|---|---|
| `playback-state` | Observed native player state, process run, local media generation, position, duration, pause, rate, and seekability |
| `media-descriptor` | Public catalog identity and optional full-file SHA-256; no local path or provider objects |
| `watch-generation` | Server-ordered watch lifecycle state, room epoch, generation, and selected logical media ID |
| `room-event` | Ordered metadata event with member/process binding and a typed payload |
| `room-snapshot` | Complete membership binding and watch state at a trusted server sequence |
| `call-capabilities` | Six explicit booleans controlling which native call controls may be exposed |
| `helper-ipc` | Requests, observations, correlated results, and errors over owned anonymous pipes |

Playback position cannot exceed a known duration. `unloaded`, `loading`, and `failed` states are paused, unseekable, and at rate 1. An unloaded player has position zero and unknown duration. A ready player can have unknown duration or be unseekable; neither property implies that playback has started. Engine seconds are converted to integer milliseconds at the helper boundary, rounding to the nearest millisecond with ties away from zero, then bounded. Negative observations are clamped to zero; non-finite or out-of-range values fail. Commands outside the contract are rejected rather than clamped.

`mediaGeneration` is allocated locally before loading or replacing media. It is distinct from the server's `watchGeneration`. Observations, commands, and acknowledgements must match the receiver's currently allocated local generation. A new process receives a new `clientRunId` even when it reopens the same movie.

A descriptor's `mediaId` is logical and opaque. `titleKey` is computed from `title` by NFC normalization, trimming and collapsing ASCII spaces, and converting ASCII `A-Z` to `a-z`; other Unicode characters remain unchanged. An empty result is invalid. A title key, duration, or file size does not prove an edition match. Equal full-file SHA-256 hashes provide strong identity; absent hashes require the explicit edition-confirmation workflow. Never insert local paths into descriptors or diagnostics.

Watch states are `idle`, `selected`, `active`, and `ended`. Idle has `mediaId: null`; other states identify a media item. Full session transition authorization belongs to the domain/service implementation. A saved local resume position does not create an active shared session.

All call capabilities default to false in an unverified host. Set a flag true only after the matching operation or observation has been demonstrated. Capability validation cannot establish provider support or participation. No provider SDK classes appear in the contract. See [Google Meet integration](google-meet.md).

## Metadata ordering and binding

The authenticated server issues `roomEpoch` and strictly increasing `sequence`. After a snapshot at sequence N, accept exactly N+1. Reject earlier or repeated events as `duplicate`; a gap requires a fresh snapshot. A different epoch requires a fresh authenticated join/snapshot. Do not advance the cursor after a rejection or use client timestamps to order events.

`selection.changed`, `watch.started`, and `watch.ended` each advance the current watch generation by exactly one. Other events retain it. A standalone watch-generation observation must match the current generation. Both lower and unexpectedly higher generations are rejected as `staleGeneration`.

An ordered `member.bound` event binds `memberId`, the new `clientRunId`, and the username actually accepted by Syncplay. Its envelope and payload identities must match. Other events must match the current member/run binding. Binding events come only from the authenticated server stream; this exception is not permission for clients to rebind themselves. Duplicate member IDs or accepted usernames in a snapshot are invalid. Catalog media IDs must be unique within a batch.

The snapshot establishes trusted epoch, cursor, generation, and member bindings after authentication. Replace this context atomically after accepting a fresh snapshot, discarding earlier pending events. The reference `Context` supplies receiver state for `accept`; never construct it from the event being checked. `accept` is pure: the consuming coordinator updates its state only after successful validation, authorization, and application. `validate` checks structure and cross-field invariants only and is insufficient at a receiving boundary.

Room events carry catalog, membership, selection, watch lifecycle, missing-media, and call-link metadata. They cannot carry play, pause, seek, rate, or readiness commands. Syncplay is the sole playback synchronization authority. `meetUri` is either null or an HTTPS Meet meeting URI without query strings or fragments; authentication redirects and browser-host navigation policies are separate concerns.

## Helper IPC and errors

Each request or observation has `id`; a response has `replyTo`. These forms are mutually exclusive. All frames include version, process run, and local media generation. The typed families are:

| Family | Correlation and payload |
|---|---|
| `bridge.hello` / `bridge.ready` | Supported version request / selected protocol and pinned Syncplay revision response |
| `sync.connect`, `disconnect`, `setReady` | TLS endpoint, room, accepted-name request, optional opaque secret handle / disconnect / readiness |
| `player.setPaused`, `setPosition`, `setSpeed`, `openFile` | Native player command; open uses an authorized opaque local media handle |
| `player.state` | Actual player observation; the payload has playback fields without duplicate envelope fields |
| `sync.connection`, `participants`, `readiness` | Engine observations; connection failure may include a sanitized typed error |
| `player.result` / `bridge.result` | Applied player state acknowledgement / applied non-player command acknowledgement |
| `bridge.error` | Correlated rejection with code, sanitized message, and retryability |
| `bridge.shutdown` / `bridge.stopped` | Owned child shutdown request / final acknowledgement |

`bridge.ready` replies to hello; `player.result` replies to player commands; `bridge.result` replies to successful sync commands; `bridge.stopped` replies to shutdown. Responses require a currently pending matching request. Request IDs use `cmd-N`, with N a positive integer up to 9,007,199,254,740,991, without leading zeros. Each sender allocates strictly increasing N within its process run; each direction has a separate sequence. Observations use opaque IDs and do not advance the command sequence.

The receiver retains a high-water mark and a bounded cache of completed results. A repeated identical retained command returns its stored result without executing again. Reuse of a retained ID with different content is `conflict`; an older ID outside the replay cache is rejected as `duplicate` and never executed again. Pending commands cannot be evicted; capacity exhaustion rejects new work with `queueFull`. Reset this state only with a new process run. Replay tracking and request correlation are coordinator requirements, not effects of schema validation. Coalesce redundant position observations only. Do not echo engine corrections as new user actions.

The reference context checks process and generation ownership. Future helper/native implementations must additionally enforce hello/ready ordering, request/result correlation, idempotency, authorization of opaque media/secret handles, bounded queues, and the shutdown lifecycle. A caller-provided path or network URL cannot be a media handle. Standard output contains protocol frames only. Never echo malformed input or secret values in errors.

| Error code | Meaning / recovery |
|---|---|
| `malformed`, `frameTooLarge`, `unsupportedVersion` | Reject invalid input; renegotiate an unsupported version or close an invalid stream |
| `foreignRun`, `staleGeneration`, `staleEpoch` | Discard obsolete work; obtain current process, media, or authenticated room state |
| `sequenceGap`, `duplicate` | Obtain a snapshot for a gap; discard a duplicate ordered event |
| `conflict`, `invalidState` | Reject contradictory identity, command reuse, or state |
| `queueFull`, `unavailable`, `timeout` | Bounded resource/provider failure; retry only when the caller and error permit it |
| `cancelled`, `internal` | Operation cancellation or sanitized internal failure |

Malformed input may not contain enough trusted context for a correlated response. Close or discard according to the owning transport; do not invent a `replyTo`. A `retryable` flag never overrides stale-state or authorization checks.

## Reproducible validation

From a clean checkout with Git and Python 3.12 or newer:

```sh
git submodule update --init --checkout --depth=1 -- 3rdparty/fastjsonschema
python3 tools/contracts/check_contracts.py
python3 -m unittest discover -s tools/contracts/tests -v
python3 tools/ci/check_repository.py
python3 -m unittest discover -s tools/ci/tests -v
git diff --check
```

No package installation is required. The validator's runtime source graph consists only of the pinned fastjsonschema module; its nested conformance-suite submodule and development extras are not runtime dependencies. [The tool manifest](../../tools/contracts/pyproject.toml) records the version, source revision, license, and commands. Repository text/syntax checks apply the shared `.editorconfig` rules to this Python tooling.

The shared corpus includes every message family, valid domain objects, malformed frames, unknown fields/versions, old process and generation examples, room sequence gaps, and prohibited playback events in the metadata channel. Additional tests exercise exact byte limits, multibyte UTF-8, duplicate keys, Unicode validity, numeric bounds, deterministic title keys, offline resolution, and ambiguous membership. Native adapters must consume the same corpus when introduced. Passing these checks establishes the contract boundary, not real playback, device compatibility, or call acceptance.
