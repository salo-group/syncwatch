# Native playback

The local playback layer owns VLC independently of the interface, synchronization helper and room service. `packages/playback-native` is a small first-party C ABI compiled against the pinned public VLC headers. It dynamically opens the verified, source-built [media SDK](libvlc.md); it does not link to an installed VLC application.

## Ownership and observations

The Swift `VLCPlayback` actor and C# `VLCPlayback` dedicated worker serialize native operations outside the UI thread. Kotlin's coroutine `VLCPlayback` implements the domain `PlaybackPort` through a bounded JVM worker and first-party JNI adapter. Cancellation can reject queued work but cannot interrupt a native operation or cancel terminal release. Native callbacks only increment an atomic sequence; no callback enters managed code or updates a view. Consumers poll validated observations of the actual player clock, duration, pause state, seekability, tracks and output dimensions. Submitting a command does not acknowledge a changed player state.

Each language boundary has a `MediaRuntime` integrity check for the complete SDK receipt, target and pinned VLC revision. Missing, modified, additional or symbolic-link members fail verification. The JVM implementation reuses the first-party strict JSON codec from `packages/ipc/jvm`. These development checks establish file integrity; authenticated immutable installation remains a separate release requirement.

A player belongs to one client run. Every replacement file advances its media generation; foreign runs, duplicate generations and stale commands or observations are rejected. Invalid loads do not consume a generation. Stop is idempotent and cannot resurrect a released media item. Close rejects new work, settles queued requests and releases native callbacks, media, player, and its runtime reference on the serialized owner. The JVM boundary reserves capacity for terminal release when its command queue is full. Cancelling an individual close wait cannot cancel resource release.

`sw_runtime` owns the verified engine instance and plugin bank independently of media players. Its explicit handle and every child player retain it; the final release destroys the bank. Existing single-player open entries remain convenience owners. The normal macOS application retains `VLCPlaybackRuntime` across End/Start so Cocoa plugin objects and queued UI work remain valid between media owners. This does not retain a released movie player or create an unused anchor player. Other consumers can use independent runtimes through the same ABI.

AppKit can retain references into the selected video module after the player and plugin bank close. The macOS adapter therefore owns one process-lifetime loader reference to the verified package's `libvout_macosx_plugin` bundle. Repeated or concurrent runtimes reuse that canonical module; a different module path is rejected. The reference also uses Apple's [documented `RTLD_NODELETE` option](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/dlopen.3.html), but the live handle is necessary to prevent a subsequent Mach-O bundle load from creating another image. Player, input, callback, thread and engine disposal still completes normally; the process releases the remaining module mapping on exit. Headless, Windows and Linux outputs keep their existing loader lifetime. The native macOS check verifies missing-module recovery, canonical aliases, concurrent reuse, retained symbol identity, foreign-module rejection and ordinary unrelated-module unloading with authored Mach-O bundles.

A local item that reaches EOF with no positive duration and a zero clock is a playback failure, including demuxers that report an empty EOF instead of an error. The application releases that failed media owner and reports the failure instead of retaining a Ready session.

Local files must have absolute paths. Network URIs are not accepted. Software decoding is the default until the selected hardware path has separately passed playback-control qualification. Volume, mute, track selection and drift-correction rate remain local operations. The synchronization coordinator will forward only the four authorized shared actions.

## Native surfaces and design

The macOS application retains its AppKit `NSView` and window until asynchronous native stop finishes. The player explicitly enables [native fullscreen behavior](https://developer.apple.com/documentation/swiftui/view/windowfullscreenbehavior(_:)) on supported systems and waits for completed native transitions during acceptance. Windows uses an `HwndHost` in the design library's video slot. Unexpected removal parks its child HWND invisibly until native release finishes, then destroys the parked window. The shared design owns layout, colors, text, control placement and action validation; the playback code supplies observations and native resources.

On Linux, `sw_playback_open_x11` accepts a nonzero `uint32_t` drawable. The pointer-based open entry rejects this output kind. `X11Display` loads the verified Xlib and calls `XInitThreads` before the first toolkit or VLC instance. JAWT acquires a realized Canvas on the event thread, validates its X11 window and releases every drawing-surface lock. The playback owner keeps the Canvas/display reference through native close; the caller must keep its parent window displayable until that receipt completes. Xlib stays loaded for the process lifetime because AWT has no independent public toolkit shutdown. This boundary requires X11; a Wayland session needs its XWayland path qualified separately.

`X11VideoCanvas` owns an independent X11 child on a dedicated display connection. Its geometry follows the parent's physical pixel bounds. Before AWT removes the Canvas peer, the child is unmapped and reparented to the root, with a server synchronization before parent destruction. Serialized native close precedes destruction of the parked child and its connection. This preserves drawable lifetime during unexpected UI detachment without blocking the event thread on the player. A surface has one playback owner, cannot be reused after close, and remains retained when native open fails. Reopening a failed owner requires closing it and creating a new owner.

VLC ignores installed-user configuration and uses the selected owned output modules. A start-paused load may expose metadata before creating a video output. Native-output checks therefore require a real video output after Play and check subsequent pause/seek observations independently. The Linux surface fixture additionally checks resize and closes the native owner before disposing its drawable. Its optional `--lifecycle-check` mode performs this lifecycle automatically. `--owned-lifecycle-check` exercises the independent child; `--owned-detach-check` also removes its AWT parent while the player remains open and verifies parking, further native observations and eventual release. These modes reject duplicate/stale surface ownership and check release after failed open. Visual observation, application integration and physical output remain separate results.

On the first seek away from zero, a paused native video may need to prepare its decoder before VLC can show the requested picture. The playback worker verifies mute and deselects audio before it starts this brief local preparation. It confirms Pause and restores the audio selection and mute state before it submits the seek. The worker stops the input if it cannot safely restore the prior state. This sequence runs inside the playback owner and sends no room commands.

On AppKit, a paused seek can also retain the picture from earlier playback. For an existing picture, the worker starts preparation at the target with a reduced rate. It restores the prior rate before it submits the final seek.

The present player layout places its toolbar and footer outside the video rectangle. Linux supplies its Canvas through the design library's `NativePlayerSurface` slot to the same declarative Player layout and presentation gates used by the native adapters. Window content minimums and video background come from shared design tokens; playback owns neither layout nor styling. WPF popovers and future overlapping controls still require the architecture's separate owned overlay-window integration and DPI/focus acceptance. The development surface does not qualify those workflows, a physical display or speaker output. Linux JNI/X11 and application-controller acceptance do not qualify visible desktop output or its runtime dependency closure.

## Development entry

After building the application and the owned SDK, launch the macOS executable with:

```sh
SyncWatch.app/Contents/MacOS/SyncWatch --local-playback /absolute/movie.mkv
```

The default media location is `Contents/Resources/Media` inside the macOS bundle, or `Media` beside the Windows application. For an unbundled build use `--media-package /absolute/sdk/payload`; Windows also accepts `--playback-bridge C:\absolute\syncwatch_playback.dll`. This explicit entry enables local playback only. Normal Library activation also uses the local WatchSession owner. Shared selection, helper/player coordination, track/settings menus, previews and Meet integration retain their own implementation gates.

The Linux `syncwatch.MainKt` development entry accepts `--local-playback`, `--media-package` and `--playback-bridge`, each followed by an absolute path. It requires the reviewed owned JVM/UI/renderer classpath and JNI bridge; there is no qualified bundled JVM installation yet. Display initialization precedes toolkit creation. The controller waits for the native surface, polls typed engine observations and completes serialized release before disposing its window.

`--playback-report /absolute/report.json` runs actual AppKit/WPF acceptance against the authored four-second fixture and exits. It checks paused load, the advancing clock, pause stability, forward/backward seek observations, native surface creation, resizing, fullscreen transitions and release. Windows `--playback-detach-probe` additionally exercises unexpected surface removal. Screen readback and physical output are reported separately; an unavailable capture does not imply presented-pixel acceptance.

Linux accepts the same report option and optional detach probe through its own application controller. Reports use a new destination and keep window observation, physical output and distribution readiness false. A desktop without an active output is reported separately from playback/control/resize results and cannot pass fullscreen acceptance. Own-application calls are functional probes, not evidence of user input or visual parity.

## Validation and CI

On the native SDK host, run:

```sh
python3 tools/playback/build.py --package /absolute/sdk/payload --output /absolute/proof
python3 tools/playback/ci.py provision
python3 tools/playback/ci.py check --gui
```

The CI check uses `artifacts/dependencies/libvlc/<target>/payload`. Its default mode validates the C lifecycle, the real C#/JNI boundary where applicable, and native application compilation. `--gui` additionally requires a GUI host for the owned AppKit/WPF surface probes. The language probes exercise Unicode filesystem paths, generation transactions, actual pause and seek observations, tracks, bounded rate values, stale commands and repeated teardown. C# also checks cancellation before a queued command executes.

Each registered libVLC CI job retains a separate playback report and development candidate, including the complete owned SDK, original sources/grants, rebuild receipts and first-party source archive. Failed consumer diagnostics are retained and fail the job. Windows candidates require the pinned developer .NET runtime; Linux artifacts contain a development JNI boundary. These candidates are not signed, authenticated, immutable or release-ready installations.

## Normal Watch integration check

An explicit `--watch-check-report /absolute/report.json` with `--watch-check-media /absolute/owned-fixture.avi` and `--state-directory /absolute/isolated-state` exercises the normal Library/Player composition. Use the authored four-second fixture and a new report path. The check selects through the Library controller, waits for actual native and presentation readiness, tests duplicate Start, Home with an advancing hidden clock, same-owner Continue, paused seek, End/release, resumed replacement, canceled preparation, and repeated close. It uses the actual native engine and common components, without automating the OS folder picker.

Inspect the report's `success`, the process exit, and retained diagnostics separately. `guiObserved`, `physicalOutputAccepted`, and `applicationBuildReady` remain false: this check does not establish manual interaction, visual parity, packaged distribution, or any of the six actual synchronization directions. Windows native checks require the existing interactive desktop; a service-session launch is insufficient.

The same explicit check supports `--watch-check-mode missing-runtime`, `load-failure`, or `close-preparing`. Use an absent SDK directory for the first, an authored invalid media file for the second, and the normal fixture for the third. These modes verify disabled Start without a runtime, failed-load release, and teardown before preparation completes. Native error dialogs are suppressed only in this explicit check so failure state can be recorded without simulated desktop input. Normal launches retain their localized error UI.
