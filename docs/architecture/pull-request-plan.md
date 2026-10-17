# Pull request implementation plan

This plan turns the [architecture](README.md) and [product requirements](product-requirements.md) into 29 reviewable implementation changes. PR01-PR24 identify product deliverables; CI01-CI05 identify continuous integration deliverables. Assign GitHub numbers when opening the corresponding pull requests. Repository CI (CI01), shared contracts (PR01), native shells (PR02), native CI (CI02), and dependency build/cache infrastructure (CI03) are implemented; later deliverables remain planned. CI03 includes the existing dependency-free validator source package; each product-engine graph still needs its own source-closure PR.

## Execution rules

- Use one focused branch and pull request per deliverable. Merge prerequisites before dependent changes; independent items may proceed once their own prerequisites are available.
- Every executable component includes its dependency manifest, formatter/linter configuration, focused tests, and reproducible setup, build, and test instructions under `docs/`.
- Establish CI01 before product implementation. CI02 and CI03 provide the native and dependency build infrastructure; every later component PR adds its real checks to that infrastructure in the same change. Follow the [CI architecture](continuous-integration.md).
- Add all external libraries, including test and build dependencies, as pinned source submodules under `3rdparty/` before building the component. Apply the [dependency policy](dependencies.md) to experiments as well as production code.
- Keep documentation in English and self-contained. Put validation results, commands, and relevant screenshots in the implementation review. Keep raw session output, private media, credentials, and device configuration outside the source tree.
- Commit and push each completed meaningful change. Keep each pull request buildable and testable within its stated scope.
- An unavailable provider capability remains an unmet requirement. Any reduced feature set, alternate call provider, or external-browser workflow needs an explicit product decision.

The order below is a dependency order rather than a calendar estimate. The early call experiment validates host feasibility. Full gate G1 also requires synchronized playback and therefore closes later during product integration.

## Sequence and dependencies

| Phase | ID | Proposed pull request title | Prerequisites |
|---|---|---|---|
| 1. Foundations and call feasibility | CI01 | Establish repository CI and required status checks | None |
| 1. Foundations and call feasibility | PR01 | Define shared contracts and repository checks | CI01 |
| 1. Foundations and call feasibility | PR02 | Bootstrap native macOS and Windows applications | PR01 |
| 1. Foundations and call feasibility | CI02 | Add macOS and Windows build and test jobs | PR02 |
| 1. Foundations and call feasibility | CI03 | Add reproducible dependency build jobs and caches | CI02 |
| 1. Foundations and call feasibility | PR03 | Pin and build the candidate call host dependencies | PR02, CI03 |
| 1. Foundations and call feasibility | PR04 | Verify Google Meet in application-owned windows | PR01, PR02, PR03 |
| 2. Shared design | PR05 | Complete the design generator source dependencies | PR01, CI03 |
| 2. Shared design | PR06 | Generate Swift and WPF design resources | PR05 |
| 2. Shared design | PR07 | Implement native shared-design components | PR02, PR06 |
| 3. Media and synchronization engines | PR08 | Package the headless Syncplay runtime | PR01, PR02, CI03 |
| 3. Media and synchronization engines | PR09 | Implement the Syncplay bridge and IPC lifecycle | PR01, PR08 |
| 3. Media and synchronization engines | PR10 | Build libVLC from pinned source dependencies | PR02, CI03 |
| 3. Media and synchronization engines | PR11 | Embed libVLC in both native clients | PR02, PR10 |
| 3. Media and synchronization engines | PR12 | Implement player controls and video overlays | PR07, PR11 |
| 3. Media and synchronization engines | PR13 | Implement previews and advanced player settings | PR12 |
| 4. Domain, catalog, and room state | PR14 | Implement watch-session state and local preferences | PR01, PR02 |
| 4. Domain, catalog, and room state | PR15 | Implement the room metadata service | PR01, PR02 |
| 4. Domain, catalog, and room state | PR16 | Implement the local library and media identity | PR11, PR14 |
| 4. Domain, catalog, and room state | PR17 | Connect room clients and exchange catalog metadata | PR09, PR14, PR15, PR16 |
| 5. Product integration | PR18 | Synchronize native playback across devices | PR09, PR12, PR14, PR17 |
| 5. Product integration | CI04 | Add integration CI and controlled device acceptance | PR18 |
| 5. Product integration | PR19 | Implement library and watch-session workflows | PR07, PR13, PR14, PR16, PR17, PR18, CI04 |
| 5. Product integration | PR20 | Integrate Meet calls and application audio policies | PR04, PR07, PR19 |
| 5. Product integration | PR21 | Harden recovery and resource ownership | PR18, PR19, PR20 |
| 6. Acceptance and distribution | PR22 | Complete localization and visual acceptance | PR19, PR20, PR21 |
| 6. Acceptance and distribution | PR23 | Package and sign reproducible desktop builds | PR21, PR22 |
| 6. Acceptance and distribution | CI05 | Automate signed candidate builds and release checks | PR23 |
| 6. Acceptance and distribution | PR24 | Add release acceptance and performance checks | PR23, CI05 |

## Phase 1: Foundations and call feasibility

### CI01: Establish repository CI and required status checks

- **Scope:** Add GitHub Actions workflows and repository-owned check scripts under `.github/workflows/` and `tools/ci/`. Run documentation, design JSON/reference, whitespace, submodule metadata, and workflow policy checks on pull requests and pushes to `main`. Add timeouts, per-change concurrency, minimal permissions, and a stable `ci/required` aggregate status. Document and apply the supported repository ruleset or branch protection only after its check names exist.
- **Validation:** Demonstrate a passing change and intentionally failing documentation, JSON, and dependency-pin fixtures. Verify that failed, canceled, or unexpectedly skipped required work cannot produce a successful aggregate. Confirm that documentation-only changes still emit the required status. Add merge-queue event support if the queue is enabled.
- **Done when:** The initial checks run on GitHub for the expected commit and the configured merge rule requires their actual result. If repository permissions or account features prevent enforcement, report it as incomplete rather than claiming that a workflow file alone protects the branch.

### PR01: Define shared contracts and repository checks

- **Scope:** Add versioned JSON schemas and small acceptance fixtures in `contracts/` for playback state, media descriptors, watch generations, room events, call capabilities, and helper IPC. Define units, error types, ordering, bounds, and schema compatibility. Add repository checks for documentation links/language, design references, and pinned dependency entries.
- **Validation:** Reject malformed, oversized, unsupported-version, and stale-generation examples. Check that valid fixtures cover the domain and IPC boundary without embedding provider SDK types. Keep validators dependency-free where practical; pin any external validator source before use.
- **Done when:** Both client implementations and the helper have a documented, testable contract to implement, and the repository checks run from a clean checkout.

### PR02: Bootstrap native macOS and Windows applications

- **Scope:** Introduce `apps/apple/`, `apps/windows/`, and native domain project skeletons. Pin supported toolchains and configure a minimal launchable window, native test entry points, formatting, and build checks. Declare the native build tools needed by later source builds.
- **Validation:** Build, launch, and close each application on its platform. Run the initial meaningful tests and formatting checks with the documented commands.
- **Done when:** A contributor can build both shells from this checkout using declared SDKs, without media or browser integrations.

### CI02: Add macOS and Windows build and test jobs

- **Scope:** Configure macOS arm64 and Windows x64 jobs with explicit supported runner images, recorded image/toolchain versions, formatter checks, native builds, and tests. Execute the same repository commands used locally. Emit test reports and unsigned build artifacts identified by commit and platform; register future server, design, and helper checks as their components arrive.
- **Validation:** Run both platform jobs on GitHub, demonstrate test/build failures reaching `ci/required`, and verify timeouts, cancellation, and artifact identity. Confirm the runner architecture instead of assuming that a macOS label implies arm64. Hosted compilation does not establish camera, hardware-decoding, or physical display acceptance.
- **Done when:** Both native shells have reproducible automated checks and downstream component PRs can extend the matrix without inventing a second local/CI build procedure.

### CI03: Add reproducible dependency build jobs and caches

- **Scope:** Add reusable job definitions, dependency manifest validation, source/artifact digest checks, exact-revision source initialization, cache keys, and cold-build entry points. Source-graph PR03, PR05, PR08, and PR10 each register their own real component build and transitive source inventory when introduced. Select rebuilds from changes to sources, pins, flags, toolchains, and workflow scripts.
- **Validation:** Test selection and cache invalidation using controlled repository fixtures, including pin, toolchain, architecture, and flag changes. As component graphs are added, run cache-free builds with implicit dependency downloads disabled after source/toolchain provisioning. Reject artifacts whose inputs or digests do not match. An unimplemented component is reported as unavailable, not as a successful build.
- **Done when:** Dependency PRs can run their builds through the shared infrastructure with correct cache isolation. Each component still has to demonstrate its own source closure before use; creating the job framework does not close G3.

### PR03: Pin and build the candidate call host dependencies

- **Scope:** Resolve the candidate Windows host source graph, including CEF, CefSharp, matching Chromium, and required transitive libraries. Add pinned submodules, build configuration, license records, and artifact/source correspondence. Define the macOS system WebKit requirements. Host selection remains provisional until PR04.
- **Validation:** Build the candidate host dependency set from the pinned sources with implicit downloads disabled after initialization. Verify architecture compatibility, notices, and browser process startup/shutdown.
- **Done when:** PR04 can use a declared, reproducible host dependency set. A required closed dependency cannot be introduced as an undocumented exception.

### PR04: Verify Google Meet in application-owned windows

- **Scope:** Add minimal native call windows using top-level provider navigation, a manually supplied meeting URL, explicit account/permission handling, and truthful call capability reporting. Document the selected host contract and remaining limitations.
- **Validation:** On two physical devices, test signed-in and guest access separately, two-way camera/microphone, device changes, compact viewport usability, close/hangup, and capture release. Exercise every capability needed for native controls, independent volume, and remote-speaking events. Do not infer participation from page load.
- **Done when:** The chosen hosts and capability set are demonstrated for the required account types, or an explicit product decision resolves any failed requirement. Full G1 remains open until PR20 and release acceptance. A failed experiment can remain a draft while independent design and playback work proceeds.

## Phase 2: Shared design

### PR05: Complete the design generator source dependencies

- **Scope:** Resolve and pin the runtime source graph of Style Dictionary. Configure local dependency resolution, the Node toolchain, and the build-tool entry point under `tools/design/`.
- **Validation:** Start the pinned generator from a clean dependency checkout with implicit registry access disabled. Verify source/license inventory and deterministic dependency resolution.
- **Done when:** PR06 can run Style Dictionary using the declared source graph.

### PR06: Generate Swift and WPF design resources

- **Scope:** Implement formatters for SwiftUI resources, typed C# values, WPF resource dictionaries, and the shared design manifest. Consume `packages/design` without maintaining separate platform token copies.
- **Validation:** Compare token IDs and values across outputs; test color alpha, logical units, references, unknown types, and stable rounding. Repeated generation must produce no changes. Compile generated resources in minimal platform projects.
- **Done when:** Both clients consume deterministic resources generated from the same design package.

### PR07: Implement native shared-design components

- **Scope:** Add reusable SwiftUI/AppKit components and WPF templates for fields, buttons, cards, menus, dialogs, status indicators, and the layouts in `components.json`. Include a small component gallery and localization infrastructure.
- **Validation:** Verify dark/light/system themes, keyboard focus, tab order, resizing, long text, and sample English/Russian strings. Review screenshots at fixed logical viewport sizes and display scales.
- **Done when:** Product screens can compose shared-design controls without hardcoding a second palette or geometry specification.

## Phase 3: Media and synchronization engines

### PR08: Package the headless Syncplay runtime

- **Scope:** Pin CPython, Twisted TLS, platform extras, and the complete required source graph. Build the embedded runtime for both platforms and introduce a headless engine startup entry point.
- **Validation:** Import and initialize the upstream engine with complete configuration without a GUI dependency. Verify TLS trust configuration, process startup/shutdown, source correspondence, and packaged runtime reproducibility.
- **Done when:** The same pinned engine can run headlessly on macOS and Windows. Player integration is added in PR09 and PR18.

### PR09: Implement the Syncplay bridge and IPC lifecycle

- **Scope:** Implement `BridgePlayer`, `BridgeUI`, the versioned pipe protocol, native transport adapters, acknowledgements, bounded queues, cancellation, and helper ownership. Connect a controlled player adapter to the real upstream engine for contract tests.
- **Validation:** Exercise partial frames, repeated commands, stale generations, foreign process runs, timeouts, backpressure, EOF, and helper crashes. Verify engine callbacks and protocol interoperability with a controlled Syncplay server. Keep playback observations distinct from user actions.
- **Done when:** Helper lifecycle and transport behavior work on both platforms; a native VLC adapter can be attached through the existing contracts.

### PR10: Build libVLC from pinned source dependencies

- **Scope:** Select the codec/filter/demuxer set required by the product, resolve its full contrib graph into submodules, and produce platform libVLC artifacts with headers, build flags, notices, and source digests.
- **Validation:** Rebuild without implicit source downloads. Probe identified 1080p/4K fixtures, embedded tracks, external audio/subtitles, and the required image/audio/subtitle filters. Verify available hardware-decoding paths.
- **Done when:** Both clients have reproducible libVLC artifacts supporting the declared media requirements, with unsupported settings identified before UI integration.

### PR11: Embed libVLC in both native clients

- **Scope:** Implement Swift C interop and C# P/Invoke adapters behind `PlaybackPort`, AppKit/Windows video surfaces, metadata probing, actual position/state events, and deterministic media release.
- **Validation:** Load, play, pause, seek, buffer, stop, and reload real files; verify actual seek results, view/handle lifetime, callbacks, hardware decoding, and cleanup on each platform.
- **Done when:** Native clients play local media without an external player window and expose accurate observations through the shared contract.

### PR12: Implement player controls and video overlays

- **Scope:** Add the native player chrome, Windows owned overlay, timeline, basic track selection, movie volume/mute, playlist navigation, context menus, keyboard actions, and fullscreen behavior.
- **Validation:** Check z-order, input hit testing, focus, display-scale changes, minimize/restore, fullscreen, key repeat, and editable-field exclusions. Confirm that controls operate on the intended player and output.
- **Done when:** Playback controls remain usable over real video on both platforms, including window and display transitions.

### PR13: Implement previews and advanced player settings

- **Scope:** Add a separate preview decoder, bounded cache, cancellation, external audio/subtitle handling, subtitle appearance, image controls, normalization, scaling, and the player-settings UI.
- **Validation:** Verify that preview never moves the main player or emits a synchronization action. Test cancellation and stale-frame rejection, 50-500 MB budgets, section reset, and the actual effect of every enabled setting.
- **Done when:** Required playback settings and previews work on both platforms; a control with no effect remains an unmet requirement.

## Phase 4: Domain, catalog, and room state

### PR14: Implement watch-session state and local preferences

- **Scope:** Implement domain state transitions in Swift and C#, versioned preference persistence, local session identity/generation, resume position, and application-facing ports. Use shared acceptance fixtures for both implementations.
- **Validation:** Test repeated Start/Continue/End, cancellation, paused seeks, stale actions, atomic persistence, invalid preferences, and application restart. A stored position must not reactivate a previous session automatically.
- **Done when:** Both domain implementations satisfy the same lifecycle contract independently of UI and network adapters.

### PR15: Implement the room metadata service

- **Scope:** Add the ASP.NET Core service with room creation/join/leave, scoped authorization, member bindings, catalog batches, ordered watch-selection events, snapshots, resume cursors, and Meet URI metadata. Define configuration, resource limits, and a reproducible local server setup.
- **Validation:** Test room isolation, expired/revoked credentials, conflicting selections, epoch/sequence handling, replay, snapshot gaps, bounded messages, and disconnect cleanup. Confirm that playback commands are not routed through this service.
- **Done when:** A clean environment can start the service and exercise its versioned API with isolated rooms.

### PR16: Implement the local library and media identity

- **Scope:** Add native folder selection and persistent grants, scanning/watching/search, media probing and cache, external asset associations, logical title matching, content hashing, and explicit edition/collision handling.
- **Validation:** Cover supported extensions, nested folders, names with multiple dots, filesystem case rules, unavailable files, moved/stale/corrupt macOS bookmarks, cache invalidation, and canceled scans/hashes. Keep full paths local.
- **Done when:** Both clients produce trustworthy, stable catalog descriptors and retain only authorized local access.

### PR17: Connect room clients and exchange catalog metadata

- **Scope:** Implement `RoomPort` in Swift and C#, catalog deltas, participant-to-Syncplay identity binding, media selection generations, missing-media state, and snapshot recovery.
- **Validation:** Join two clients, exchange catalogs, reject ambiguous matches and stale events, resolve the accepted Syncplay username, reconnect after a sequence gap, and verify room isolation. No descriptor may open an arbitrary local path.
- **Done when:** Participants agree on an authorized local media selection and generation before playback starts.

## Phase 5: Product integration

### PR18: Synchronize native playback across devices

- **Scope:** Attach the bridge to native VLC, coordinate room selection and readiness, apply engine corrections, and connect shared playback to the domain lifecycle. Add standard-client interoperability coverage.
- **Validation:** Verify macOS-to-Windows play/pause/seek, competing actions, paused seeks, late join, missing/incompatible media, readiness, server restart, and helper failure. Measure drift using a common time basis and distinguish user actions from engine corrections.
- **Done when:** Real native clients synchronize the agreed media, reject obsolete actions, and recover without replaying old seeks. Core G4 behavior is demonstrated.

### CI04: Add integration CI and controlled device acceptance

- **Scope:** Automate a controlled Syncplay server, RoomMetadata service, packaged helper, native adapters, and deterministic media fixtures. Cover process lifecycle, session ordering, synchronized playback, and reconnection on compatible CI runners. Add a separate manually triggered, trusted device-acceptance path for interactive video/camera/microphone/provider checks. Extend it with Meet integration in PR20.
- **Validation:** Exercise isolated rooms, missing media, latency/disconnection scenarios, helper/server failures, and stop/start cleanup. Confirm that virtual media, mocked capabilities, and headless execution cannot mark physical or Meet acceptance complete. Test that untrusted PR code cannot execute on personal device runners or access meeting/signing credentials.
- **Done when:** Automated integration checks run reproducibly and fail the appropriate required status, while physical/provider results retain a distinct acceptance status and revision. G1/G2 remain open until the necessary device evidence exists.

### PR19: Implement library and watch-session workflows

- **Scope:** Compose the library, selected-media details, room indicators, activity view, Start/Continue/End actions, player navigation, and persisted general/player settings using the completed native components and services.
- **Validation:** Run complete local and two-device workflows, return to the library while retaining playback, restore the active player, end the session, and restart the application. Check saved-state feedback and all settings actions.
- **Done when:** Users can complete the viewing workflow on either client without service-specific controls leaking into the product UI.

### PR20: Integrate Meet calls and application audio policies

- **Scope:** Integrate the verified call hosts and `CallPort` into product navigation, implement the owned call shell, device/media controls, independent call volume, movie ducking, and call state reporting through proven capabilities.
- **Validation:** Repeat the call capability matrix inside the assembled product while VLC and Syncplay operate between devices. Verify account access, two-way media, device changes, window actions, independent volume, the 0.35 ducking multiplier and 280 ms release delay, hangup, and capture release.
- **Done when:** G1 requirements work in the assembled application or an explicit product decision resolves each missing capability. Window navigation alone cannot close F13, F14, F21, or F22.

### PR21: Harden recovery and resource ownership

- **Scope:** Complete `ConnectionSupervisor`, independent channel recovery, interrupted-session presentation, bounded retries, cancellation, stale-command handling, and cross-component shutdown. Basic cleanup must already work in the component PRs; this change covers their combined lifecycle.
- **Validation:** Interrupt Syncplay, metadata, and Meet independently; deny permissions; terminate the helper; change files during decoding; close windows during pending operations; repeat stop/start cycles. Verify pause/rate reset on synchronization loss and no pause on call-only failure.
- **Done when:** Recovery preserves the active generation and leaves no orphan processes, hidden capture, unbounded queues, or unreleased access grants.

## Phase 6: Acceptance and distribution

### PR22: Complete localization and visual acceptance

- **Scope:** Complete English/Russian UI strings, theme propagation, responsive layouts, component-state coverage, and native rendering adjustments against the shared design specification.
- **Validation:** Review the required screens and interaction states in dark/light/system themes, supported UI languages, fixed logical viewports, and multiple display scales. Check keyboard access, long filenames, menus, call shell, and video overlays while masking private media content.
- **Done when:** G5 has reviewed native evidence, and fixes use the common design contracts rather than divergent platform token values.

### PR23: Package and sign reproducible desktop builds

- **Scope:** Add macOS bundle signing/notarization and Windows package signing, embedded helper/media/browser packaging, build automation, dependency notices, binary/source manifests, and clean-install instructions. Keep signing material outside the checkout.
- **Validation:** Build from a clean clone, verify complete source pins and dependency closure, exercise offline component rebuilds, check signatures, and install/launch/close on clean supported environments. Verify first-run permissions, upgrades, and repeated startup.
- **Done when:** Signed candidate packages are reproducible from declared inputs, contain required notices, and pass clean-machine smoke checks. G3 is closed for the distributed module set.

### CI05: Automate signed candidate builds and release checks

- **Scope:** Run the PR23 packaging commands in a trusted release workflow for a selected commit or tag. Build both platforms, validate notices and source manifests, sign/notarize using environment-scoped credentials, and produce candidate artifacts with digests. Keep pull request builds unsigned. Separate candidate generation from publication; the workflow does not publish packages automatically.
- **Validation:** Verify selected-revision checkout, artifact input digests, signatures, failed-signing behavior, missing credentials, immutable artifact identity, and separation from untrusted jobs/caches. Confirm that candidate acceptance refers to the exact package digests and records physical/provider checks separately from hosted checks.
- **Done when:** A trusted trigger produces verifiable macOS and Windows candidates or fails explicitly. Signing, build, or required acceptance failures block release eligibility; publication remains a separate authorized operation.

### PR24: Add release acceptance and performance checks

- **Scope:** Add reproducible end-to-end and measurement tooling, controlled media fixture definitions, failure scenarios, release checks, and explicit performance budgets. Keep evidence with the release review rather than committing raw session output or private media.
- **Validation:** Run F01-F25 against the signed candidates on two physical devices: 1080p and 4K playback, simultaneous real calls, synchronization races, independent network interruptions, stop/start, resource release, and restarts. Evaluate the provisional p95 drift target of 250 ms with a valid clock/RTT method; establish CPU/GPU/memory budgets from comparable measurements.
- **Done when:** G1-G5 and the full requirement matrix have passing release evidence or explicitly approved scope decisions. A failed runtime check remains a release blocker; successful source or document checks do not replace it.

## Requirement coverage

Every row receives final release acceptance in PR24. The table identifies the implementation changes that own each requirement.

| Requirement | Implementation pull requests |
|---|---|
| F01 | PR16, PR19 |
| F02 | PR10, PR11, PR16 |
| F03 | PR11, PR16, PR19 |
| F04 | PR16 |
| F05 | PR16, PR17, PR18 |
| F06 | PR17, PR18, PR19 |
| F07 | PR14, PR18, PR19 |
| F08 | PR09, PR18, PR21 |
| F09 | PR17, PR18 |
| F10 | PR13 |
| F11 | PR12, PR18, PR19 |
| F12 | PR12, PR13 |
| F13 | PR12, PR14, PR20 |
| F14 | PR20 |
| F15 | PR12, PR19 |
| F16 | PR12, PR22 |
| F17 | PR06, PR07, PR14, PR22 |
| F18 | PR14, PR19 |
| F19 | PR13, PR14, PR19 |
| F20 | PR13, PR14 |
| F21 | PR04, PR20, PR21 |
| F22 | PR04, PR20 |
| F23 | PR18, PR21 |
| F24 | PR16, PR21 |
| F25 | PR09, PR11, PR13, PR20, PR21, PR23 |

## Gate closure

| Gate | Early risk checks | Complete acceptance |
|---|---|---|
| G1: Google Meet | PR03 and PR04 establish host and account/capability feasibility | PR20 validates assembled calling; PR24 rechecks the signed product under combined load |
| G2: VLC functionality | PR10-PR13 verify modules, adapters, controls, and settings | PR18 validates synchronized operation; PR24 validates the signed product and combined media load |
| G3: Dependency closure | PR03, PR05, PR08, and PR10 close each external source graph before use | PR23 verifies the distributed dependency set; PR24 accepts the resulting packages |
| G4: Syncplay bridge | PR08 and PR09 validate runtime, protocol, and helper lifecycle | PR18 and PR21 validate native synchronization and recovery; PR24 checks the signed product |
| G5: Visual acceptance | PR06 and PR07 establish common resources and native components | PR22 validates the product UI; PR24 checks the distributed applications |

CI01 provides the required repository status, CI02 runs native checks, CI03 validates dependency builds, CI04 supplies automated integration and separate device acceptance, and CI05 builds release candidates. These jobs carry the relevant gate evidence without replacing unmet runtime or physical-device requirements.

## Pull request review contract

Each implementation review states its planning ID, concrete behavior change, touched components, prerequisites, requirement IDs, validation commands and results, screenshots for UI changes, remaining gaps, and any new dependency pins. Include user-reproducible manual steps for device or provider checks. A checkpoint uses an existing implementation review; it does not require an empty pull request.

If a source graph or native implementation is too large for one review, split it into smaller buildable changes while retaining the parent planning ID in their descriptions and updating dependency links. Keep platform-neutral contract changes together and verify both clients against them before merging.
