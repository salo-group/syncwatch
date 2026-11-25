# Standalone development application packages

Application packaging uses the common first-party staging policy in
`tools/application/candidate_tree.py`. Platform adapters supply explicit application,
media, helper and runtime components. This policy does not build dependencies or
download runtimes.

The assembler reserves a new output directory, inventories its inputs, copies and
checks their bytes and executable modes, then publishes `contents` and an atomic
`candidate.json` receipt. Existing outputs are never adopted or replaced. Failure
removes only the newly reserved output. Component destinations cannot escape,
collide or overwrite an application's existing assets. Nested runtime components
are ordered after their application directory. Relative links must remain inside
their component; Windows components reject links.

The receipt records the source commit, native target, component inventories and
actual candidate files. It declares a development candidate, with release readiness
false. Local byte integrity does not authenticate a release or seal a mutable
filesystem against concurrent modification.
Native recipes also record the exact first-party source file hashes and modes,
including local overlays and new untracked inputs. An overlaid checkout's Git base
commit is never presented as the complete identity of those sources. Preparation
fails if that source snapshot changes before staging.

Swift builds and tests run only on macOS, C# desktop builds and tests only on
Windows, and Kotlin builds and tests only on Linux. The shared Apache-2.0 Design
package stays independent of packaging. External product sources remain pinned
Git submodules under `3rdparty/`.

The staging policy is checked on all three native CI lanes:

```sh
python3 -m unittest discover -s tools/application/tests -v
```

These checks cover relocation, Unicode paths, unchanged inputs, existing user data,
contained links, partial copy failure and input mutation. They do not qualify an
installer, an application window or playback. Runtime closure, relocated launch, restart and interoperability acceptance are
checked separately from these staging regressions.
The developer JDK described in `tools/jvm/toolchains.json` is not an application
runtime input. The Linux adapter requires the separately pinned, source-built JVM
described below.

## Native recipes

`tools/application/package.py` runs only on its requested native OS/architecture.
Its build-time inputs are explicit; the resulting application's normal launch
does not require media, helper, bridge or developer runtime flags.

| Target | Application input | Bundled runtime locations |
| --- | --- | --- |
| macOS | Built Swift executable | `SyncWatch.app/Contents/Resources/{Media,Syncplay}`; first-party playback ABI statically linked |
| Windows | WPF project and declared .NET SDK | All published assemblies, `Media`, `Syncplay`, first-party playback DLL and private `Runtime` beside `SyncWatch.exe` |
| Linux | Production application JAR | `Media`, `Syncplay`, JNI/ABI libraries, source-built `Runtime`, reviewed `Desktop` JARs, `Renderer` and dynamic X11 `Clients` beside the native launcher |

For example, on macOS:

```sh
python3 tools/application/package.py --target macos-arm64 \
  --application apps/macos/.build/debug/SyncWatch \
  --media-package /path/to/verified-media-payload \
  --helper-package /path/to/verified-helper-package --output /path/to/new-candidate
```

The macOS recipe seals the completed development bundle with an ad hoc
signature. It verifies the signature and checks that the copied runtime
trees retain their original bytes and modes. The app can still read legacy
development packages stored under `Contents/Frameworks`. Distribution
requires separate publisher signing and notarization.

Windows publishing uses the standard WinExe apphost with `AppHostRelativeDotNet`
and `AppHostDotNetSearch=AppRelative`. SDK and framework runtime versions belong to
`tools/native/toolchains.json`. Global .NET installations are not searched. The
SDK-provided platform runtime grants accompany its selected framework directories.

`tools/application/build_linux.py` compiles only the production owners declared
in `apps/linux/application.json`; tests are not shipped as application classes.
Every runtime JAR is checked against the existing reviewed Linux compiler output.
The native launcher resolves its own executable directory, supplies owned X11,
font, audio and library locations, and invokes its private Java executable. It
preserves application arguments and removes ambient JVM options/classpath overrides.

## Application JVM source profile

`tools/application/jvm-profile.json` pins OpenJDK21 and the additional ALSA/X11
clients and build-only CUPS/Xt headers as registered Git submodules. OpenJDK's
bundled native sources and per-module grants remain in that original snapshot.
`jvm_sources.py` exports and verifies original Git blobs, modes and grants;
`jvm_runtime.py` checks their pinned archive digests before an offline Linux build.
The shared source-profile reader checks the indexed Gitlinks, portable export
boundaries, retained grants and original build entry before export, build or
receipt acceptance. Native CI runs the same source-policy regressions on all
three operating systems without compiling a client on another platform.
The verified Temurin SDK is used only to bootstrap compilation. It is not copied
into the resulting runtime image.

The runtime retains corresponding original source archives, OpenJDK's root grants
and every selected module's legal files. GPL/Classpath-exception and other original
terms remain attached to their respective files; the product's Apache license does
not replace them. The source-built X11/font clients come from the declared native
SDK graph. Native import closure, packaging CI, relocated GUI launch and all six
interoperability directions remain qualification gates. A successful source build
or a locally assembled candidate alone does not clear those gates.

`linux-clients.json` separately pins Xcursor, Xfixes and Xinerama, which AWT loads
dynamically. `linux_clients.py` builds them against the same verified Media SDK as
the JVM and preserves their original source archives and grants. The Linux adapter
requires this component and rejects a different source or unverified Media build relationship. The
launcher supplies its `Clients/lib` directory alongside the other owned libraries.
Runtime qualification must inspect loaded libraries after opening the folder
picker and playing video: direct ELF imports alone do not cover dynamic loading.

## CI

A development package may explicitly provide `--runtime-build-media` when its
verified Media SDK differs from the SDK used to build the existing JVM and X11
clients. The original SDK must match both retained build-receipt hashes exactly.
Packaging verifies both complete inventories, the original source identities and
every header, native library and shared-data file; only VLC's plugin directory is
outside this linkage comparison. Any added, removed or changed linkage input
requires rebuilding the runtimes. An accepted comparison retains the original
Media receipt and an exact compatibility inventory in the application package.
The original JVM/X11 receipts remain unchanged. This permits a codec-plugin
update without recompiling unrelated runtimes or weakening their provenance.

Repository CI first builds and verifies the selected dependency matrix. The macOS, Windows and Linux jobs then consume SDKs and the native helper produced by that exact workflow run and checked-out commit. A consumer may reuse a successful producer from an earlier attempt of the same run. Cross-run reuse remains restricted to the separate trusted-main dependency cache policy.

Artifact transport checks repository/run identity, exact commit-scoped names, producer attempt, size and SHA-256. Import checks the fresh verification source/tree, declared toolchains, source Gitlinks, first-party build inputs and complete payload bytes. Helper import additionally checks its native probe and protocol acceptance receipt. Portable archive validation and streaming extraction are shared with dependency builds; validation logs stay outside the product runtime.

`tools/application/ci.py` coordinates native assembly. Linux prepares configure macros from the verified Media SDK's original Xorg source bytes, builds its JVM/X11 closure offline, and compiles only declared production application sources. The provisioned build SDK's GNU config and pkg-config macro bytes are recorded in the JVM receipt.

Native validation artifacts contain `candidate/contents/`, `candidate/candidate.json`, the validation report, the outer build manifest and applicable Design renders. The outer manifest binds the candidate receipt; archive publication rechecks each runtime file while streaming it. An absent or changed package cannot produce successful native validation. The artifact preserves executable modes. Individual SDK/helper imports remain bounded to 256 MiB; complete native application uploads have a separate 512 MiB bound in the shared artifact policy. Uploads and downloads stream their bytes with bounded buffers; full application ZIPs are never loaded into one memory buffer.

`applicationBuilt` describes completed assembly. `guiAccepted` and `physicalOutputAccepted` remain false in hosted CI: real-window interaction, audio/video output, error recovery and all six directed synchronization paths require separate acceptance on the actual native clients. Installation, authentication, Google Meet and full functional/visual parity are not implied by a successful package build.

### Verify a native archive set

Use the archive-set check to prepare input for release packaging. Obtain the
expected commit, head, tree, run ID and SHA-256 values from the reviewed CI
run before downloading the three archives. Use the publisher's archive
digests. A hash calculated from a downloaded file cannot establish its origin.

Create a JSON request with these fields. Replace each placeholder with the
value from the reviewed run. Resolve relative archive paths from the request file directory.

```json
{
  "sourceCommit": "REVIEWED_BUILD_COMMIT",
  "sourceHead": "REVIEWED_HEAD_COMMIT",
  "sourceTree": "REVIEWED_BUILD_TREE",
  "runId": "RUN_ID",
  "archives": {
    "macos": {"path": "macos.zip", "sha256": "MACOS_ARCHIVE_SHA256"},
    "windows": {"path": "windows.zip", "sha256": "WINDOWS_ARCHIVE_SHA256"},
    "linux": {"path": "linux.zip", "sha256": "LINUX_ARCHIVE_SHA256"}
  }
}
```

```sh
python3 -B tools/application/candidate_set.py request.json
```

The check reads the archives without extracting them. It verifies the
reviewed source and run, each complete file inventory, the payload bytes,
and the files and modes needed for native launch. It emits a JSON receipt after all three archives pass.
A successful native job from an earlier attempt of the same run is valid.
The receipt identifies development inputs. Release platform trust and
clean-install acceptance are separate from this check.
