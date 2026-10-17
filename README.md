# SyncWatch

SyncWatch is a desktop application for watching local movies together with synchronized playback and Google Meet calls. It uses Swift on macOS, C# on Windows, VLC for playback, and Syncplay for synchronization.

[Project documentation](docs/README.md) defines the product requirements, shared design system, architecture, dependency policy, and implementation plan.

External source dependencies are pinned Git submodules in `3rdparty/`:

```sh
git clone --recurse-submodules https://github.com/salo-group/syncwatch.git
cd syncwatch
git submodule update --init --recursive
```

The repository contains architecture specifications, the platform-neutral design package, shared contracts in `contracts/`, pinned dependency sources, and validation tooling in `tools/ci/` and `tools/contracts/`. See [development setup](docs/development.md) for reproducible checks and [shared contracts](docs/architecture/contracts.md) for schemas and acceptance fixtures. Native application implementation and media runtime validation are pending.
