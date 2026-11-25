# Desktop UI JVM source profile

`compose-ui-jvm` builds sixteen original JVM modules: Skiko AWT, five Compose UI primitives, three lifecycle/viewmodel modules, two navigation-event modules, UI, animation core, foundation layout, animation and foundation. The owned offline recipe and application/design consumer run only on Linux x64. SwiftUI client checks run only on macOS and WPF client checks only on Windows.

All enabled upstream source files, build metadata and license inputs are inventoried in `tools/jvm/compose_ui.json`. External sources are pinned Git submodules under `3rdparty/`. The UI, lifecycle UI, navigation-event and Skiko selections are Apache-2.0; the complete inherited composition-state, serialization, concurrency, annotations, Unicode and standard-library grants remain in their parent profiles. The original root grants and selected build declarations accompany every artifact. The profile records the selected files that rely on the repository grant rather than an initial Apache block header. Original source headers are retained without rewriting.

Common, intermediate and desktop source fragments keep their upstream refinement graph. The owned Kotlin SDK supplies compiler-only plugins; it supplies no application runtime JARs. Skiko's `Version.kt` is generated from its original pinned generator template and the declared deployment/Skia tags. Both the template and generated output are retained in the source archive. The separate JBR API build uses the pinned upstream processor and retains the original notices.

The Skiko build copy of `Display.kt` normalizes nonpositive or nonfinite Linux refresh rates to the existing upstream fallback. An output with no active mode can otherwise suspend the frame limiter indefinitely and leave video interop bounds stale after resize. Valid refresh rates are preserved. The configured file, modification notice and unchanged original accompany the source archive; its bytecode has reviewed pins for all three build hosts. The consumer tests invalid/valid rates and repeated frame-limiter wakeups without loading a native renderer.

The dependency recipe builds its complete owned parent graph, compiles the sixteen modules without Gradle/Maven or binary downloads, and retains originals, generated/configured source, notices and the compiler profile. Verification rejects missing or extra payloads, changed notices, changed generation/configuration and any JAR that differs from the target's reviewed compiler output. The consumer compiles the actual application, shared design adapters and assertions, then executes the common presentation corpus, display scheduling and Kotlin playback value/cancellation checks in a headless JVM.

```sh
python3 tools/dependencies/build.py prepare --component compose-ui-jvm --platform linux-x64
python3 tools/dependencies/build.py build --component compose-ui-jvm --platform linux-x64 --cold
```

This component qualifies JVM bytecode and its first-party consumer. Native Skiko/Skia, platform libraries, fonts, video surfaces, packaging and visible application acceptance are independent requirements. `linux-compose-ui` remains unavailable until that complete native/runtime graph and application are qualified. Passing this component does not enable Linux application or physical synchronization acceptance.
