# SyncWatch

SyncWatch is a desktop application for watching local movies together with synchronized playback and Google Meet calls. It uses Swift on macOS, C# on Windows, VLC for playback, and Syncplay for synchronization.

[Project documentation](docs/README.md) defines the product requirements, shared design system, architecture, dependency policy, and implementation plan.

External source dependencies are pinned Git submodules in `3rdparty/`:

```sh
git clone --recurse-submodules https://github.com/salo-group/syncwatch.git
cd syncwatch
git submodule update --init --recursive
```

The repository contains native shells in `apps/apple/` and `apps/windows/`, domain modules in `packages/apple-domain/` and `packages/windows-domain/`, the platform-neutral design package, shared contracts in `contracts/`, pinned dependency sources, and validation/build tooling under `tools/`. See [native development](docs/native-development.md) for toolchains, formatting, builds, tests, and launch commands; [development setup](docs/development.md) covers repository checks. Player, synchronization, call, and complete product UI integrations remain planned.
