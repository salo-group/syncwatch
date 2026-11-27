# Embedded Python runtime

The `cpython-embedded` component builds CPython 3.12.15 from the pinned `3rdparty/cpython` source and zlib 1.3.2 from `3rdparty/zlib`. The pinned `3rdparty/python-unicode-data` supplies the original Unicode 15.0 data and complete grant for the core's type tables. Supported targets are Apple Silicon macOS, Windows x64 and Linux x64. The [source profile](../tools/python-build/profile.json) owns exact original file hashes, executable modes, complete notices, module membership and standard-library selection.

The runtime contains the interpreter core, frozen import/bootstrap modules, the upstream bootstrap builtins and selected JSON, context, opcode, array, math, socket/select and compression modules. Windows also retains the registry builtin required by upstream import initialization. Other optional extensions are disabled. The selected standard library is an owned source ZIP; Python's installed development SDK and its standard library are never packaged.

Build and verify on the native target:

```sh
python3 tools/dependencies/build.py build --component cpython-embedded --platform macos-arm64 --cold
python3 tools/dependencies/build.py build --component cpython-embedded --platform macos-arm64
```

Use `windows-x64` or `linux-x64` on those targets, and `python` on Windows. The declared C SDK, CMake, make or MSBuild and a release CPython development toolchain are build tools. Builds make no package-manager or upstream external-library downloads. Original frozen-module inputs are compiled by the source-built pinned freeze/bootstrap programs; upstream generator scripts run as build tools. Configuration changes select modules, prefix static compression symbols, set stable metadata and make the shared library relocatable. Original interpreter and compression algorithms remain unchanged.

The build compiles the original CPython build information source. On macOS and Linux the compiler uses `SOURCE_DATE_EPOCH`. On Windows an MSBuild target supplies the matching date and time to that source file. The build checks the file against its original bytes. The native consumer reads the timestamp from the loaded library.

The Unix build loads a separate SyncWatch Make file after CPython's generated Makefile. The native link uses the selected static libraries and a relative library identity on macOS. The build rejects unresolved dynamic lookups and verifies that the configure output and source bytes are unchanged.

The package contains the shared interpreter library, embedding headers, platform configuration, selected stdlib, Windows socket/select extensions and import library, complete notices, selected unchanged sources and separate configuration derivatives. It excludes build SDKs, test executables, debug symbols and the standalone Python command. The receipt records source profile, SDK identity, file hashes, native dependencies and actual startup proof. Cold builds establish source correspondence; a cache restore rechecks every file and recompiles a native consumer against the relocated owned package.

The native consumer verifies the loaded library's actual path and exact Python version. Seven startup/lifetime scenarios cover isolation from environment/current-directory injection, Unicode paths, invalid initialization, foreign ownership, wrong-thread shutdown, script failure and finalization failure. Owned-library execution additionally checks JSON, bounded threaded socket framing, numeric/Unicode cases and pinned zlib compression/version. Native imports are restricted to platform SDK libraries and the declared runtime. Corrupt, extra and missing package files fail verification.

This is a development embedding component. It does not provide TLS, the complete Twisted/Syncplay dependency graph, a production Syncplay provider, authenticated immutable installation, signing or application release packaging. The socket exercise verifies interpreter facilities; real Syncplay interoperability remains a separate integration gate. Shared-library byte reproducibility across independent cold builds is not yet an acceptance claim.

See [distribution obligations](licenses.md) and the [startup contract](python-startup.md).
