# Owned call-host experiment

This directory defines fixed inputs for an isolated first-party CEF host experiment on macOS, Windows and Linux.
Preparation is in progress; no host executable exists yet.
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

The initial policy explicitly disables ScreenAI OCR, ScreenAI extraction and MainNodeAnnotations.
Normal component and certificate-data updating remains enabled.
This is a candidate configuration; runtime behavior and persistent reuse remain unqualified.

Preparation and compilation must not launch the browser engine.
The current browser policy requires a specific owner exception before native browser testing.
No launch command is implemented or authorized by this document.

Before product adoption, close every qualification item in `inputs.json`.
Retain complete notices, exact source/artifact correspondence and actual native evidence.
A version header, archive digest or successful compilation does not qualify Google Meet.
