# SyncWatch documentation

SyncWatch lets people watch their local movie files together with synchronized playback and a Google Meet call in a separate application-owned window.

## Application

- [Application behavior](application.md): screens, controls, settings, keyboard actions, window behavior, and connection recovery.
- [Running the room service](room-service.md): service setup, configuration, and startup.
- [Building and running](building.md): source checkout, declared toolchains, native builds, and launch commands.
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
- [Google Meet integration](architecture/google-meet.md): application-owned call windows, authentication, capabilities, and browser-host limitations.
- [Dependencies](architecture/dependencies.md): pinned Git submodules and source dependency policy.

## Technology choices

| Area | Technology |
|---|---|
| macOS client | Swift, SwiftUI, AppKit |
| Linux client (planned) | Kotlin/JVM, Compose Multiplatform Desktop; dependency closure and implementation pending |
| Windows client | C#, WPF, .NET 10 LTS |
| Shared design | `packages/design`, Apache 2.0, generated with Style Dictionary |
| Movie playback | VLC/libVLC |
| Playback synchronization | Syncplay |
| Calls | Google Meet in an application-owned window |
| Room metadata service | C#, ASP.NET Core, HTTPS/WSS |
| External source dependencies | Pinned Git submodules under `3rdparty/` |

Native application shells, domain ownership guards, generated design resources, and shared contracts are present. The shared Library workspace, application-settings dialog, core Player component and PlayerSettings dialog run in SwiftUI/WPF, with local language/theme persistence and opt-in component galleries. Kotlin resources and Compose Library/Settings/Player/PlayerSettings source are present, with the Linux build still unavailable. The source-built headless Syncplay/TLS runtime and development helper/pipe transports are present. Actual player integration, application synchronization provider activation, and calling remain incomplete. See [helper ownership and native pipes](syncplay-bridge.md). Application documentation describes the intended interface; the existing shells do not yet provide these workflows. Provider and dependency limitations are described in the architecture.

- [Local library ownership and scanning](architecture/library.md)
- [Native playback ownership, development entry and acceptance](playback.md)
