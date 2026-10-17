# SyncWatch documentation

SyncWatch lets people watch their local movie files together with synchronized playback and a Google Meet call in a separate application-owned window.

## Product specification

- [Development setup](development.md): repository checks, tooling requirements, and initial CI behavior.
- [Product requirements](architecture/product-requirements.md): library, playback, synchronization, calls, settings, and resource lifecycle.
- [Application architecture](architecture/README.md): components, state ownership, native clients, and integration gates.
- [Shared contracts](architecture/contracts.md): versioned schemas, wire units/bounds, ordering, IPC, and acceptance fixtures.
- [Shared design system](architecture/design-system.md): platform-neutral design contracts and generated native resources.
- [Syncplay and room metadata](architecture/syncplay.md): engine integration, local IPC, media selection, and reconnect behavior.
- [Google Meet integration](architecture/google-meet.md): application-owned call windows, authentication, capabilities, and feasibility criteria.
- [Dependencies](architecture/dependencies.md): pinned Git submodules and source dependency policy.
- [Implementation plan](architecture/delivery-plan.md): deliverables and acceptance criteria.
- [Pull request plan](architecture/pull-request-plan.md): 24 product and 5 CI changes, prerequisites, requirement coverage, and gate closure.
- [Continuous integration](architecture/continuous-integration.md): required checks, platform builds, dependency caches, integration acceptance, and release candidates.
- [Validation strategy](architecture/validation.md): document, contract, platform, and physical-device checks.

## Technology choices

| Area | Technology |
|---|---|
| macOS client | Swift, SwiftUI, AppKit |
| Windows client | C#, WPF, .NET 10 LTS |
| Shared design | `packages/design`, Apache 2.0, generated with Style Dictionary |
| Movie playback | VLC/libVLC |
| Playback synchronization | Syncplay |
| Calls | Google Meet in an application-owned window |
| Room metadata service | C#, ASP.NET Core, HTTPS/WSS |
| External source dependencies | Pinned Git submodules under `3rdparty/` |

The repository contains specifications, design data, shared contracts, offline contract validation, repository CI, and dependency sources. Native applications, generated platform resources, and runtime integration are not implemented yet. Meet embedding, complete VLC feature support, and dependency closure remain explicit acceptance gates.
