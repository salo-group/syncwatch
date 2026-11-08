# Owned call-host experiment

This directory defines fixed inputs for an isolated first-party CEF host experiment on macOS, Windows and Linux.
The first-party host starts as a blank-surface experiment.
It is not an adopted application runtime or a supported calling feature.
The native application clients remain Swift, C# and Kotlin on their respective operating systems.

`inputs.json` fixes one candidate version, three archive identities and the corresponding declared source roots.
The CEF, Chromium and WTL sources are unmodified pinned Git submodules.
Chromium's declared nested sources still require selected graph preparation and qualification.
WTL's header correspondence is distinct from legal and final browser-package qualification.

The experimental host will use only the public CEF interface and supported settings.
It must retain the browser sandbox and certificate validation.
Its initial surface is blank, with capture denied and no application credentials.
It must own its profile, windows, child processes and complete shutdown.
Window dimensions come from the shared Design source.
No existing browser profile may be imported.
Each macOS experiment creates a unique OSCrypt service and account in Keychain.
The profile records their identifiers for cleanup, without recording the encryption key.
After all owned processes exit, remove only that experiment's Keychain item and profile.

The initial policy explicitly disables ScreenAI OCR, ScreenAI extraction and MainNodeAnnotations.
Normal component and certificate-data updating remains enabled.
The host enables Chromium's network-service sandbox on Windows and Linux.
macOS enables this sandbox independently of that feature switch.
Native process observations must verify the applied restrictions and detect unsandboxed fallback.
This is a candidate configuration; runtime behavior and persistent reuse remain unqualified.

Preparation and compilation must not launch the browser engine.
The current browser policy requires a specific owner exception before native browser testing.
This document does not authorize execution or supply a launch procedure.

Before product adoption, close every qualification item in `inputs.json`.
Retain complete notices, exact source/artifact correspondence and actual native evidence.
A version header, archive digest or successful compilation does not qualify Google Meet.

## Compile-only preparation

While source qualification remains open, compilation requires an explicit research exception.
This exception does not authorize product adoption.

1. Obtain the native archive identified in `inputs.json`.
2. Run `python3 tools/cef-host-experiment/prepare.py --archive ARCHIVE --output PREPARED`.
3. After research approval, configure CMake with `SYNCWATCH_CEF_RESEARCH_BUILD=ON`.
4. Set `CEF_ROOT` to the returned SDK directory.
5. Set `PROJECT_ARCH=arm64` on macOS or `PROJECT_ARCH=x86_64` on Linux.
6. Use the native x64 Visual Studio generator on Windows.
7. Build the Release configuration with at most two parallel compiler jobs.

The preparation command verifies the fixed digest and size before extraction.
It rejects unsafe archive paths and preserves complete vendor notices.
It records each prepared file without executing vendor code.
CMake reads dimensions and the window title from shared Design sources.
The host requires a new installation root and rejects reuse of an existing directory.
By default, it rejects other navigation, popups, downloads, file dialogs and capture permissions.
Its browser lifetime remains separate from the native application clients.

Compilation does not prove normal close, sandbox behavior or Google compatibility.
Verify all child processes, capture and audio release during authorized native experiments.
Delete only the experiment-owned installation root after every child process exits.

## Bounded Google compatibility research

The separate `SYNCWATCH_CEF_RESEARCH_GOOGLE_NAVIGATION` CMake option defaults off.
After lifecycle and sandbox acceptance, an approved research run may enable this option.
The host then opens the public Meet landing endpoint and its Google sign-in redirect.
Document navigation permits only `about:blank` and HTTPS on `meet.google.com` or `accounts.google.com`.
The public CEF URL parser validates origins, default ports and absent URL credentials.
Document URLs longer than 8192 code units are rejected.
This policy does not restrict all subresource requests.
Capture, popups, downloads, external protocols and file dialogs remain unavailable.
Host diagnostics record only load/error codes and navigation rejection.
URLs, queries, credentials and page contents are not written to host diagnostics.
Provider rejection must remain visible; never bypass Google policy or change the user agent.

The `navigation-policy` CTest exercises the same public policy on each native operating system.
It uses CEF's URL parser without starting the browser engine.
Run `ctest --test-dir BUILD -C Release --output-on-failure` after the native build.
