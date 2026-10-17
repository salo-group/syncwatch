# External dependencies

All external library sources are pinned Git submodules under `3rdparty/`. `.gitmodules` defines upstream locations, and Git links define the exact revisions used by the product.

## Pinned source modules

| Submodule | Version / commit | Responsibility | Source license |
|---|---|---|---|
| `3rdparty/style-dictionary` | `v5.7.0` / `745264a62d01037581c6654fb4ac66bb3d245f07` | Generate platform resources from shared design data | Apache 2.0; `LICENSE` and `NOTICE` |
| `3rdparty/syncplay` | `v1.7.6` / `993232ab095bb810593459bc705b3e6fc64ad161` | Playback synchronization client engine and server | Apache 2.0; bundled files may have separate notices |
| `3rdparty/vlc` | `3.0.24` / `6de05adcbaf2e8b85fe86aad4169393098628119` | Embedded libVLC and C API headers | LGPL 2.1 in `COPYING.LIB`; the full application and individual modules have additional license terms |
| `3rdparty/fastjsonschema` | `v2.21.2` / `4f1ed6d9c0462f3522a66f49c8f3dc482300101b` | Offline Draft 7 contract validation from pinned Python source | BSD 3-Clause; `LICENSE` |

The Apache 2.0 requirement applies to the shared design library and Style Dictionary. It does not change VLC's license. The planned native adapters use Swift C interoperability and C# P/Invoke directly; VLCKit and LibVLCSharp are not included.

Style Dictionary is a build tool. The native UI does not require a JavaScript runtime for design resources. Its pinned `package.json` requires Node.js `>=22.0.0`.

The contract checker imports fastjsonschema directly from its submodule and verifies its revision, Git index pin, and source version. Its runtime graph has no third-party dependencies. Python 3.12 or newer is a developer/CI toolchain requirement. No pip packages are installed. The upstream nested JSON-Schema-Test-Suite submodule and development extras are unused by this checker. See the [tool manifest](../../tools/contracts/pyproject.toml) and [contract validation commands](contracts.md#reproducible-validation).

## Complete source graph

Every external library, including transitive dependencies, must have its source in a pinned submodule under `3rdparty/`. A top-level source checkout does not satisfy this rule for all of its dependencies. Registry packages and local caches do not replace source pins.

Before building a component:

1. Resolve its complete runtime and build dependency graph for the selected configuration.
2. Record upstream URLs, exact revisions, licenses, and scope.
3. Add the corresponding source submodules and configure builds to use local sources.
4. Associate distributed binaries with their source revisions, build flags, toolchain, and SHA-256 digests.
5. Disable implicit dependency downloads and verify the notices for the resulting binary module set.

Compilers, platform SDKs, and system frameworks such as SwiftUI, AppKit, WebKit, .NET, and WPF are build-platform requirements. Google Meet is a remote service rather than a vendored source library.

## Dependency closure still required

| Component | Required source graph | Status |
|---|---|---|
| Headless Syncplay bridge | CPython, Twisted TLS, and the platform-specific transitive graph, including declared requirements such as `certifi` and `pem` | Versions and source submodules are not resolved yet |
| Design generator | Runtime dependencies of the pinned Style Dictionary package and their dependencies | Top-level source is pinned; transitive sources are pending |
| VLC build | Selected codecs, demuxers, filters, and other contrib libraries | Module selection and source graph are pending; implicit contrib downloads must be replaced with pinned sources |
| Windows Meet host | Open-source candidate: CEF, CefSharp, matching Chromium sources, and their dependencies | Host selection depends on the call integration gate |

This table describes the known areas of incomplete dependency closure. It is not a complete software bill of materials or a claim that offline builds are ready.

## Closed browser SDKs

WebView2 is a possible Windows host, but its closed runtime and SDK cannot be represented by a source submodule; samples are not the library. It is not selected under the current source policy. Adopting it requires an explicit policy change. CEF and CefSharp offer source access but do not establish Google authentication compatibility or Meet support by themselves.

## Working with submodules

Run from the repository root:

```sh
git submodule update --init --recursive
git submodule status --recursive
git diff --submodule=short
```

For each update, review the release and licenses, check out an exact revision, update the dependency specification, validate the affected components, and commit and push the completed change. Reproducible builds must not use `git submodule update --remote`.

## Upstream references

- [Style Dictionary source and license](https://github.com/style-dictionary/style-dictionary).
- [Syncplay source and license](https://github.com/Syncplay/syncplay).
- [VideoLAN libVLC](https://www.videolan.org/vlc/libvlc.html).
- [fastjsonschema source and license](https://github.com/horejsek/python-fastjsonschema).
- [CEF](https://github.com/chromiumembedded/cef) and [CefSharp](https://github.com/cefsharp/CefSharp).
- [WebView2 runtime distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).
