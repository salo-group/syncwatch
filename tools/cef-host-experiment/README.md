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
An approved launcher may set `SYNCWATCH_CEF_EXPERIMENT_MEETING_URL` to one canonical Meet meeting URL.
Only HTTPS `meet.google.com` URLs with a lowercase `aaa-bbbb-ccc` code are accepted.
Explicit ports, URL credentials, queries, fragments, escaped codes and other paths are rejected.
An absent value preserves the configured page. An empty or invalid value fails before root creation and browser initialization.
Blank-only builds reject this input. Keep its value outside arguments, packages, source and diagnostics.
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

The macOS entrypoint requests normal application activation after CEF initializes.
This supports keyboard and mouse input during an explicitly launched research session.
It does not change desktop settings or add remote focus automation.

## Linux encryption metadata research

`SYNCWATCH_CEF_RESEARCH_LINUX_KEY_PROBE` defaults off and requires a native Linux blank-only build.
It cannot run with Google navigation enabled. Its page remains blank and capture stays denied.
An approved launcher must supply the separately verified private Secret Service environment.
Before profile creation, startup requires private HOME/XDG/control paths and the exact scope-local Unix bus socket.
The probe selects the supported `gnome-libsecret` backend. It cannot fall back to the desktop bus.
The probe requests only the cumulative `OSCrypt.FreedesktopSecretKeyProvider.InitStatus` histogram through public CEF APIs.
It uses no DevTools frontend, remote-debugging endpoint or page bridge.
It accepts one structurally valid provider sample and logs only its numeric status and attempt count.
Responses are limited to4096 bytes, at most20 attempts and a15-second deadline.
Provider success0 returns exit0 after normal close. Unknown, malformed, failed or interrupted results cannot qualify credential entry.
The launcher must verify every owned process exits, then remove the private service, profile and temporary deployment.
This initialization result does not qualify all data encryption, filesystem confinement, Google authentication or product adoption.

Linux Google research automatically requires the same private environment and provider check.
It starts blank and opens the validated target only after its own status0 result.
Failed or unknown initialization closes the window before a Google credential page can appear.
Success preserves the Google window until normal close or the approved launcher's lifetime limit.
The standalone blank diagnostic option remains incompatible with Google navigation.
Neither mode reads or imports a desktop browser profile.

## Synthetic cookie restart probe

`SYNCWATCH_CEF_RESEARCH_COOKIE_PROBE` defaults off. Configure a separate blank build with this option. It cannot enable Google navigation or the Linux histogram probe. It opens no browser window and requests no page.

1. Create a fresh, private test scope. On macOS, name it `cookie-probe-` followed by 32 random lowercase hexadecimal digits.
2. On Linux, run the probe in the approved private Secret Service and sandbox environment. Keep the same private bus and unlocked collection for both launches.
3. Run `python3 tests/run_cookie_store_probe.py NATIVE_EXECUTABLE PRIVATE_SCOPE` from this directory using the native desktop account. Point to the prepared research executable.
4. Confirm both processes exit with code zero and the synthetic row has ciphertext. On Linux, verify the private Libsecret provider format as well.
5. Confirm the owned processes have stopped. Remove the disposable scope and, on macOS, only the generated Keychain item. On Linux, also remove the temporary deployment and private services.

The probe uses one fixed HTTPS `.invalid` origin and the public CEF cookie manager. The first process writes and flushes an expiring marker. The second reopens the same profile and verifies the marker. Keep the standard session-cookie expiry policy. The probe does not authorize retention of Google credentials or qualify real Meet restart.

To verify restart of the key service, run with `--phase write`, stop both owned services, and restart them with the same protected store. Then run with `--phase read` against the same scope. Confirm the first browser process has exited before stopping its key service.
