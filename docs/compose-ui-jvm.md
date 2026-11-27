# Desktop UI JVM source profile

`compose-ui-jvm` builds sixteen original JVM modules: Skiko AWT, five Compose UI primitives, three lifecycle/viewmodel modules, two navigation-event modules, UI, animation core, foundation layout, animation and foundation. The owned offline recipe and application/design consumer run only on Linux x64. SwiftUI client checks run only on macOS and WPF client checks only on Windows.

All enabled upstream source files, build metadata and license inputs are inventoried in `tools/jvm/compose_ui.json`. External sources are pinned Git submodules under `3rdparty/`. The UI, lifecycle UI, navigation-event and Skiko selections are Apache-2.0; the complete inherited composition-state, serialization, concurrency, annotations, Unicode and standard-library grants remain in their parent profiles. The original root grants and selected build declarations accompany every artifact. The profile records the selected files that rely on the repository grant rather than an initial Apache block header. Original source headers are retained without rewriting.

Common, intermediate and desktop source fragments keep their upstream refinement graph. The owned Kotlin SDK supplies compiler-only plugins; it supplies no application runtime JARs. Skiko's `Version.kt` is generated from its original pinned generator template and the declared deployment/Skia tags. Both the template and generated output are retained in the source archive. The separate JBR API build uses the pinned upstream processor and retains the original notices.

The recipe compiles the original Skiko display code. The SyncWatch Linux launcher sets `skiko.vsync.enabled=false`. This avoids waiting for a frame when XRandR reports no active mode. It leaves Skiko's OpenGL and software renderer selection in place. The setting also disables Skiko's GLX swap synchronization. The desktop compositor can still pace presentation. Test frame pacing on each supported display environment.

The dependency recipe builds its complete owned parent graph. It compiles the sixteen modules without Gradle, Maven, or binary downloads. It retains original sources, the generated version file, notices, and the compiler profile. Verification rejects extra or missing payloads, changed source or notices, changed generation, and any JAR that differs from the reviewed Linux output. The consumer compiles the application and shared design adapters. It runs common presentation and Kotlin playback tests in a headless JVM.

```sh
python3 tools/dependencies/build.py prepare --component compose-ui-jvm --platform linux-x64
python3 tools/dependencies/build.py build --component compose-ui-jvm --platform linux-x64 --cold
```

The Linux display probe uses Xvfb and `xrandr` to check redraw at 60 Hz, at zero Hz, and after recovery. It runs through the native launcher. Use the verified desktop SDK, the packaged Renderer directory, and the developer JDK.

```sh
python3 tools/application/check_linux_display.py --desktop-sdk /absolute/sdk/payload \
  --renderer /absolute/SyncWatch/Renderer --jdk /absolute/jdk --xvfb /usr/bin/Xvfb
```

This component qualifies JVM bytecode and its first-party consumer. Native Skiko/Skia, platform libraries, fonts, video surfaces, packaging and visible application acceptance are independent requirements. `linux-compose-ui` remains unavailable until that complete native/runtime graph and application are qualified. Passing this component does not enable Linux application or physical synchronization acceptance.
