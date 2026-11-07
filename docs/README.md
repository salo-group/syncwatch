# SyncWatch documentation

SyncWatch lets people watch their local movie files together with synchronized playback and a Google Meet call opened in a supported browser or the official Meet PWA.

## Application

- [Application behavior](application.md): screens, controls, settings, keyboard actions, window behavior, and connection recovery.
- [Running the room service](room-service.md): service setup, configuration, and startup.
- [Google sign-in and Meet setup](google-meet-setup.md): developer registration, protected credentials, test users, and browser/PWA requirements.
- [Building and running](building.md): source checkout, declared toolchains, native builds, and launch commands.
- [Continuous integration](continuous-integration.md): change selection, required results and native acceptance boundaries.
- [Building the design tools](design-generator.md): source-only Style Dictionary profile and API.
- [Inspecting JVM dependencies](jvm-dependencies.md): offline runtime metadata inventory before source adoption.
- [Building JVM annotations](jvm-annotations.md): pinned Java sources, compiler SDK and verified JAR artifacts.
- [Building JBR API](jbr-api.md): optional runtime-service interfaces, source generation and ordinary-JDK fallback checks.
- [Kotlin stdlib sources](kotlin-stdlib-sources.md): reviewed source package, complete notices and pinned Unicode titlecase provenance.
- [Compose JVM runtime](compose-runtime.md): source-owned composition, annotations and collections with a compiler-transformed consumer on the three developer targets.
- [Desktop composition state](compose-state.md): source-owned serialization, saveable state, lifecycle and Swing dispatcher with an event-thread-confined state owner.
- [Python package verification](python-package.md): shared native inventory, filesystem and startup path boundary.
- [Native hash library](native-hashing.md): pinned HACL sources, original runtime-header provenance and verified static artifacts.
- [Licenses and distribution](licenses.md): first-party licensing and third-party notices.

## Architecture and design

- [Application architecture](architecture/README.md): components, state ownership, native clients, and integration limitations.
- [Shared contracts](architecture/contracts.md): versioned schemas, wire units/bounds, ordering, IPC, and acceptance fixtures.
- [Local watch state](architecture/local-state.md): media ownership, Start/Continue/End, resume history, local preferences, and atomic persistence.
- [Shared design system](architecture/design-system.md): platform-neutral design contracts and generated native resources.
- [Room service API](architecture/room-service.md): authorization, catalog batches, snapshots, ordered events, and limits.
- [Google accounts, rooms, and calls](architecture/accounts-and-rooms.md): target sign-in, room admission, automatic Meet creation, session security, and provider boundaries.
- [Syncplay and room metadata](architecture/syncplay.md): engine integration, local IPC, media selection, and reconnect behavior.
- [Google Meet integration](architecture/google-meet.md): room-to-browser/PWA handoff, authentication, capabilities, and lifecycle boundaries.
- [Dependencies](architecture/dependencies.md): pinned Git submodules and source dependency policy.

## Technology choices

| Area | Technology |
|---|---|
| macOS client | Swift, SwiftUI, AppKit |
| Linux client | Kotlin/JVM, Compose Multiplatform Desktop |
| Windows client | C#, WPF, .NET 10 LTS |
| Shared design | `packages/design`, Apache 2.0, generated with Style Dictionary |
| Movie playback | VLC/libVLC |
| Playback synchronization | Syncplay |
| Calls | Google Meet in a supported browser or official PWA; SyncWatch manages the room and intended provisioning/handoff |
| Room metadata service | C#, ASP.NET Core, HTTPS/WSS |
| External source dependencies | Pinned Git submodules under `3rdparty/` |

The three native clients contain Library, local WatchSession/VLC ownership, generated Design, room transport, and a source-built Syncplay helper integration. Account sign-in, automatic Meet provisioning, and normal room-to-browser/PWA call handoff remain unimplemented. Component galleries are design fixtures rather than provider acceptance. See [application behavior](application.md), [helper ownership](syncplay-bridge.md), and the integration limitations in the architecture.

- [Local library ownership and scanning](architecture/library.md)
- [Native playback ownership, development entry and acceptance](playback.md)

- [Account service](account-service.md): confidential Google sign-in, proof-bound native handoff, device sessions and current implementation limits.
