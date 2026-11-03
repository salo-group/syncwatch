# Native libVLC SDK

The `libvlc` component builds VLC 3.0.24 and its selected codec, audio, text and font dependencies from the exact Git submodules registered in `3rdparty/`. It targets native macOS arm64, Windows x64 and Linux x64. This SDK is consumed by the separate [native playback adapters](playback.md). Native output modules are selected for AppKit, Windows and Linux X11/PulseAudio; physical output remains separately unqualified.

## Decoder reset ownership

The avformat demuxer derives its media clock from audio and video streams. Sparse subtitle cues cannot hold that clock at the first cue, starve an attached audio input or leave video waiting at end of input. The configured derivative filters both clock bounds by stream category while retaining the original timestamps and packet processing. The native regression combines embedded Matroska tracks with an external WAV and SRT, and requires selected-stream retention, fresh PCM, decoded video, real clock progression and normal shutdown after paused seeks. These remain decoded-output checks, not physical-output acceptance.

The common VLC source recipe makes full decoder and preroll resets discard every queued picture, including undated preview pictures. The upstream threshold excluded those pictures; repeated paused seeks after playback could fill the picture pool, stop video decoding and block a later Playing seek and shutdown. The configured derivative changes only the two full-reset thresholds. Timestamp-selective flushes, playback authority and client controls retain their existing boundaries. The original LGPL source remains intact in its archive; the configured source and change notice accompany the SDK.

Every native SDK build and revalidation runs a first-party sparse-video regression: warm playback, pause, thirty fractional seeks, resume and a Playing seek. It requires fresh decoded-video statistics, advancing native time and normal stop/release. An advancing timer or repeated display of an old frame cannot pass. The bounded child is joined and its temporary media/executable are removed. This headless regression does not qualify native windows or physical output.

## Build and revalidate

Provision developer tools before preparing the offline source graph:

```sh
python3 tools/vlc/provision_sdks.py
python3 tools/dependencies/build.py prepare --component libvlc --platform macos-arm64
python3 tools/dependencies/build.py build --component libvlc --platform macos-arm64 --cold
python3 tools/vlc/runtime.py verify --platform macos-arm64
```

Use `linux-x64` on Linux. On Windows use `python` and `windows-x64`. CI provisions its developer SDK before running the same prepare/build/verify entry points. Preparation exports exact original Git blobs, including complete selected notices, rather than filtered checkout bytes. The recipe disables automatic downloads, Meson wraps, FFmpeg autodetection and network support. A restored dependency cache must pass the complete source, grant, payload, native-import, codec-configuration and fresh-consumer checks again.

Fresh SDK verification also compiles the current first-party playback C ABI and runs its real engine, file-input and loaded-content probes on the native host. These cover the actual clock/events, scoped generations, release, pinned content reads, filesystem invalidation or Windows write exclusion, and handle ownership. Consumer source hashes and receipts are retained under `native-verify/first-party-playback`; authored media and probe executables are removed after the check. The SDK payload is verified again afterward. Consumer code remains separate from SDK payload/cache identity, so a cache hit still checks the current application ABI. This does not qualify managed adapters, native windows, physical output or complete shared-room admission.

macOS uses the selected Apple SDK plus GNU M4, Autoconf, Automake, Libtool, Gettext, Bison, Flex, Gperf, pkgconf, CMake and Ninja. Linux also needs NASM, GNU config helpers and patchelf. Developer provisioning is an explicit online step; the application libraries are built offline from their source pins. Windows uses a separate MSYS2 UCRT developer SDK with the base archive and every installed package fixed by digest in `tools/vlc/windows-sdk.json`; provisioning leaves the bootstrap SDK intact. An explicitly selected existing Windows developer SDK can be provided through `SYNCWATCH_VLC_MSYS_ROOT`. Compiler support notices remain separate from application-library grants. No SDK zlib, iconv or winpthread binary becomes an application dependency.

## Selected sources and media behavior

`tools/vlc/profile.json` records the exact files, original executable inventory, notices, source edges and plugin selection. The graph contains VLC, FFmpeg, dav1d, source-built zlib, FreeType, HarfBuzz, FriBidi, libass, libsamplerate, Fontconfig, Expat, GNU libiconv, Meson, the separately pinned Unicode authority and source-built winpthreads for Windows. Fontconfig/Expat serve Linux; GNU iconv serves Windows; CoreText and DirectWrite are platform font providers. Font files come from the OS or user and are not bundled by this component.

Linux retains Fontconfig's original default alias, matching, hinting and fallback rules from its pinned source. The configuration is relocated into the owned package and checked against those source bytes on every verification. The consumer selects that configuration directory explicitly; it does not rely on a configuration left in the build prefix.

The Linux output source graph adds the existing pinned Xorg/XCB client sources, PulseAudio17.0 client libraries and libsndfile1.2.2. The shared X11 recipe is used by both the renderer and media SDK. VLC selects software `xcb_x11`, its window provider and `pulse`; its private XCB/Pulse helpers and PulseAudio's private client library are packaged explicitly. PulseAudio's daemon, device modules and optional D-Bus/GLib/X11 discovery are disabled. libsndfile's external Xiph/MPEG codecs are disabled. Complete original LGPL, Apache, embedded GSM/Sun and other grants remain in the source/notices inventories. One daemon-only ALSA mixer sample with a nonportable filename is excluded; the exact path and reason are in the profile.

Linux consumers set `PULSE_CLIENTCONFIG` to the owned `share/pulse/client.conf`, which disables autospawn and connects only to an existing user audio server. This changes no user or system configuration. X11, display-server/session, audio-device and drawable-lifetime behavior require separate native output and application checks. This profile does not select native Wayland, XVideo or GL/VA/VDPAU output.

PulseAudio's original release-version script receives its distribution `.tarball-version` input, validated against the pinned NEWS release header. That configured metadata accompanies the original sources; it cannot inherit an enclosing application's Git version. Windows SDK bootstrap decodes the verified xz archive with developer Python before invoking GNU tar, so it needs no ambient compression executable.

FFmpeg uses dynamic libraries with GPL, version3 and nonfree components disabled. The actual libraries must report the expected license, version and configuration through their own APIs. FreeType uses its FreeType License choice. The full original grants and embedded notices are retained, including license alternatives for excluded original sources.

Fresh native consumers check authored I420/PCM, H.264/AAC, 1080p H.264/AAC and 4K HEVC/AAC fixtures. They observe metadata, decoded frames, resampled PCM and its known tone, pause stability, forward and backward seek pixels, stop, reload and release. Separate effect cases check actual brightness changes, normalization amplitude, pitch preservation during time stretching, replacement by an external audio tone and rendered SRT/ASS subtitle pixels. These are memory-callback observations; they establish no physical display, speaker, GPU-surface or application acceptance.

Windows plugins reside under `bin/plugins`; Darwin/Linux plugins reside under `lib/vlc/plugins`, matching each native loader's default discovery. No `VLC_PLUGIN_PATH` environment override is required.

On Windows, the consumer loads the verified owned shared-library closure before initializing VLC. Its dependency search permits only the owned DLL directory and System32, and it rejects a loaded library with a foreign path. Handles remain owned until all players and the VLC instance are released. This is required because VLC's plugin loader restricts dependency discovery to System32; a developer SDK or working-directory search must not provide missing application libraries.

VideoToolbox/CVPX modules are selected on macOS, and D3D11VA on Windows. Hardware availability and playback controls need separate qualification. H.264 VideoToolbox backward seeking is unqualified; software decoding remains the accepted consumer path. The Linux software decoder and newly selected X11/PulseAudio output sources do not by themselves qualify native surfaces, output or the full Compose application/runtime.

## Corresponding sources and declared changes

The payload contains headers, owned dynamic libraries, selected plugins, full notices, per-source original ZIPs, exact configured VLC files, first-party rebuild recipes, source inventories, compiler SDK identity, command flags, import closure and a payload digest report. ZIP entries preserve original bytes; executable modes are reconstructed from the retained profile inventory. The SDK and its build executables are not distributed as application libraries. Dynamic dependencies must remain replaceable when packaging an application.

VLC configuration derivatives have explicit source correspondence and dated notices. `src/revision.txt` records the base source pin. `configure.ac` uses that original commit's date instead of the current clock. `lib/media_player.c` inherits an explicitly configured audio-filter list when creating a player: the original empty variable otherwise masks the initialization setting, leaving normalization inactive. `modules/common.am` links Windows plugins to the owned shared codec libraries while retaining static compiler support, replacing the upstream assumption that every contrib library is static. The changed files retain their original grants and a dated change notice. The exact configured files accompany the original sources; this is a modified build of the recorded base revision.

The GNU iconv mirror's pinned MSVC patch is reversed only in an isolated source tree. Every restored file must match the complete original GNU release inventory. The libraries and public headers are built; the GPL command-line program is not packaged. macOS selects VLC's original pipe/dup fallbacks for its deployment floor. VLC uses C11 explicitly, and its Windows configure step sees the Windows 10 API baseline so that its synchronization import-library check matches the compiler headers.
