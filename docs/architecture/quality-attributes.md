# Security and performance requirements

SyncWatch must maximize practical security and measured performance on macOS, Windows and Linux.
These requirements apply to design, implementation, verification, packaging and updates.
Correctness, security, playback quality, shared Design and free operation constrain every optimization.
Absolute security or optimal performance must never be claimed without evidence.

## Security

1. Use secure defaults, least privilege and explicit trust boundaries.
   Isolate browser content, media decoding, local IPC and authorized file access.
   Preserve sandboxing, certificate validation and operating-system protections.
2. Authenticate each account and authorize each room, call and synchronization operation.
   Enforce scope, expiry, revocation and request ordering.
   Reject stale, foreign, replayed and malformed requests.
3. Bound input sizes, queues, concurrency, request rates and wait times.
   Treat files, network messages, browser content and IPC peers as untrusted inputs.
4. Store secrets in the operating-system credential store or encrypted server storage.
   Exclude credentials, private paths and call content from logs, artifacts and diagnostic exports.
   Keep Google browser sessions separate from application credentials.
5. Request capture and file permissions for explicit user actions.
   Verify denial, retry, revocation and actual capture shutdown.
   Call close, hangup, failure and application exit must release owned capture and audio.
6. Pin complete dependency sources and verify artifact identity, correspondence and notices.
   Assess applicable vulnerabilities and maintain a secure update path.
   Block the affected delivery when a critical security defect remains unresolved.
7. Maintain a threat model for connected features and their trust boundaries.
   Verify negative authorization paths and recovery after rejected input.
   Record residual risks and the evidence supporting each security claim.

## Performance

1. Record a working baseline before optimization.
   Profile the actual user scenario and address a measured bottleneck.
   Repeat equivalent correctness, security and performance checks after each change.
2. Measure cold and warm startup, UI latency, CPU/GPU use, memory, disk and network activity.
   Measure frame drops, playback timing, call latency, reconnect and shutdown where applicable.
   Use representative agreed media, library sizes and call conditions.
3. Define budgets and acceptable variation for each supported device and scenario.
   Record the hardware, software revisions, settings, workload, sample count and result spread.
   Compare equivalent conditions and distinguish application, provider and test-environment limits.
4. Keep blocking I/O and heavy processing outside the UI thread.
   Use bounded queues, caches and concurrency with explicit ownership and cancellation.
   Use hardware acceleration when measurements confirm correctness and benefit.
5. Verify sustained use, repeated sessions, file replacement, reconnect and application exit.
   Owned processes, capture, connections, descriptors and memory must return to a measured idle state.
   Investigate retained resources and unexplained material regressions before delivery.

## Acceptance

Each feature records its applicable security checks and performance budgets.
Run affected native checks on Swift/macOS, C#/Windows and Kotlin/Linux.
For network changes, measure the applicable behavior in all six directed client combinations.
Retain a baseline, results, cleanup evidence and unresolved limits.

An enabled flag, successful build or isolated benchmark does not establish readiness.
Unverified requirements remain open in delivery tracking.
Existing feature qualification does not automatically establish these new quality requirements.

See [dependency policy](dependencies.md), [account and room boundaries](accounts-and-rooms.md),
[playback ownership](../playback.md) and [Meet integration](google-meet.md).
