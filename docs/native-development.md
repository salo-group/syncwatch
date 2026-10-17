# Native development

SyncWatch has a SwiftUI macOS shell in `apps/apple/` and a C#/WPF Windows shell in `apps/windows/`. Their UI-independent ownership guards live in `packages/apple-domain/` and `packages/windows-domain/`. Each application owns a fresh process run and an initially unloaded media generation. The shells open a neutral native window with the product title; shared design resources and components arrive through PR06/PR07. Player, synchronization, room, and call integrations remain separate planned components.

## Toolchains and dependencies

[The toolchain manifest](../tools/native/toolchains.json) defines the Apple compatibility floor and exact Windows SDK. Apple packages use Swift tools 6.1, Swift language mode 6, macOS 13 deployment, and Xcode 16.3 or newer. The initial native acceptance lanes target macOS arm64 and Windows 11 x64. The check commands assert the actual host architecture and print installed toolchain versions.

The Windows solution uses .NET SDK 10.0.402 and C# 14. [global.json](../global.json) disables SDK roll-forward and prerelease selection. Install this exact SDK even when a different .NET SDK/runtime already exists. The WPF project targets `net10.0-windows`; its domain library and executable tests target `net10.0`.

The shells have no external library dependencies. SwiftUI, AppKit, Foundation, Swift Testing, WPF, .NET reference/runtime packs, compiler analyzers, and the bundled formatting tools belong to the platform SDK/toolchain exception in the [dependency policy](architecture/dependencies.md). Apple uses only a local Swift package dependency. Windows uses local project references, with registry/audit sources cleared in [NuGet.Config](../NuGet.Config). Restore resolves installed SDK packs; it must not fetch library packages. Future libraries require pinned sources in `3rdparty/` before integration.

## macOS

Install/select a compatible Xcode with its macOS SDK and command-line tools, then run from the repository root:

```sh
python3 tools/native/check_apple.py
open artifacts/apple/SyncWatch.app
```

The checker runs SDK-provided `swift format lint --strict` with [.swift-format](../.swift-format), runs domain tests with warnings treated as errors, builds the SwiftUI executable, and assembles a development `.app` bundle with the checked-in Info.plist. SwiftPM builds only local packages. The bundle is a development artifact; release signing, notarization, icons, and distribution are introduced by PR23.

For individual commands:

```sh
SYNCWATCH_CONTRACT_FIXTURES="$PWD/contracts/fixtures/acceptance.json" swift test --package-path packages/apple-domain -Xswiftc -warnings-as-errors
swift build --package-path apps/apple -Xswiftc -warnings-as-errors
```

Apply formatting with `swift format format --in-place --configuration .swift-format <source-files>`. Use tracked/first-party source files rather than walking generated `.build` content. The build checker selects files through Git.

## Windows

Install the exact Windows x64 SDK from [Microsoft's .NET 10 download page](https://dotnet.microsoft.com/en-us/download/dotnet/10.0). An isolated installation is also supported: obtain the SDK archive declared in `tools/native/toolchains.json`, verify its SHA-512 against `sdkArchiveSha512`, and extract it to a local toolchain directory. Do not commit downloaded SDKs or generated files. See [SDK selection](https://learn.microsoft.com/en-us/dotnet/core/tools/global-json).

From the repository root in Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/native/check_windows.ps1
dotnet apps/windows/SyncWatch/bin/Debug/net10.0-windows/SyncWatch.dll
```

`-ExecutionPolicy Bypass` applies only to this PowerShell process running the repository-owned script; no machine/user execution policy is changed. When using an isolated SDK, pass its executable using `-DotNet <sdk-directory>\dotnet.exe` and invoke that same executable for the application DLL.

The checker restores the three local projects using the empty-source NuGet configuration, runs SDK-provided `dotnet format --verify-no-changes`, builds with analyzers and warnings treated as errors, then runs the domain test executable. WPF enables PerMonitorV2 DPI awareness, runs without elevation, and shuts down when its main window closes.

Equivalent individual commands, using the selected SDK:

```powershell
dotnet restore SyncWatch.slnx --configfile NuGet.Config
dotnet format SyncWatch.slnx --no-restore --verify-no-changes
dotnet build SyncWatch.slnx --no-restore --configuration Debug
$env:SYNCWATCH_CONTRACT_FIXTURES = Join-Path $PWD 'contracts/fixtures/acceptance.json'
dotnet run --project packages/windows-domain/SyncWatch.Domain.Tests --no-build --no-restore --configuration Debug
```

The domain tests use a repository-owned executable assertion harness and return a failing exit code on any failure; `dotnet test` is not their entry point. No external test framework is downloaded. A future framework integration must follow the source dependency policy. Apply formatting with `dotnet format SyncWatch.slnx --no-restore` before rerunning the checker.

## Acceptance boundary

Both domain implementations consume the same four run/generation ownership examples from `contracts/fixtures/acceptance.json`. They also check media replacement, process restart, the safe-integer generation limit, and invalid process IDs. These guards cover ownership only; they are not complete wire decoders or session coordinators. The [contract reference checker](architecture/contracts.md) remains the conformance oracle until each native boundary is implemented.

For interactive shell acceptance, open each built application in a logged-in desktop session, confirm a visible window titled SyncWatch, resize it, close the window, and quit the application if its platform retains the process. Verify there is no remaining owned application process after quitting. Capture the visible window for its implementation review. A successful background build or remote process start alone does not establish visible-window acceptance.

Build outputs, SDKs, caches, and screenshots stay outside tracked sources. Hosted checks do not establish interactive, media, or call acceptance.

## Native CI and artifacts

The required native jobs run on `macos-15` arm64 with Xcode 26.3 and `windows-2025` x64 with .NET SDK 10.0.402. The [toolchain manifest](../tools/native/toolchains.json) declares these selections. Runtime architecture checks reject an incompatible image. Runner image versions and actual toolchain versions are recorded because hosted images can change. CI selects the exact Xcode installation; local development can use a newer compatible Xcode.

`python3 tools/ci/native_job.py apple` wraps the same Apple checker. On Windows, `python tools/ci/native_job.py windows` wraps the same PowerShell checker and provisions the exact SDK from its SHA-512-verified official archive if unavailable. The Windows wrapper uses `RUNNER_TEMP` for isolated SDK provisioning; set it to an untracked temporary directory when invoking this CI wrapper locally. Neither native build installs library packages.

Each job retains a ZIP under a platform/commit/run/attempt-specific artifact name for at most seven days. A successful archive contains an unsigned development application, `build-manifest.json`, and `validation.xml`. The manifest identifies the tested commit/tree, pull-request head, runner, toolchains, run, result, and SHA-256 of every payload file. Pull-request checks build GitHub's merge revision. The Apple bundle is nested in `SyncWatch.app.tar` to preserve executable permissions; extract it before launching. Windows output is framework-dependent and needs a compatible .NET Desktop Runtime.

The XML reports validation stages (architecture, toolchain, native checks, unchanged source), rather than individual native test cases. Native assertion output remains in the job's test output. A failed build or test returns failure and retains a report-only archive when preparation is possible. Upload failure also fails the job. Checkout failure, cancellation, or a hard timeout can prevent retention. Unsigned hosted artifacts are not release packages; their manifests identify inputs and bytes without claiming signing or bit-for-bit reproducibility.
