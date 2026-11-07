# Application behavior

This guide describes the intended SyncWatch interface on macOS, Windows, and Linux, using the [shared component contracts](../packages/design/components.json). Library, local playback, shared watch, account/room flows, and Meet provisioning have native implementations. Complete functional qualification remains open. The required owned Meet host is unimplemented; see [native playback](playback.md) and [Meet integration](architecture/google-meet.md).

## Library and watch session

The main window has a header, a text-focused movie-library column, a selected-movie panel, room status, and recent activity. Layout, dimensions, dark/light appearance, and control composition come from the common design library.

Users choose a local movie folder, scan subfolders, search, rescan, and enable automatic refresh. Distinguish empty, scanning, searching, selected-media, inaccessible-folder, and missing-media states. Show available duration, resolution, audio tracks, and subtitles. Match external audio/subtitle files deterministically.

**Start watching** is available when the local media runtime is verified. The selected live movie shows **Continue watching** and **End watching** instead. Returning to the library retains playback; ending releases local player and preview resources. A new application launch may restore a saved position without silently restarting a watch session. Ending locally does not issue an extra peer playback action.

Participants identify the selected movie or playlist item through permitted descriptors and content identity, without sharing absolute file paths or movie bytes. Missing or incompatible media is actionable and never implies readiness.

## Shared playback boundary

Only four user actions change a peer's playback: **play, pause, seek, and select a movie/playlist item**. Skip uses the resulting absolute position, so participants can choose different local skip intervals.

Volume, mute, audio/subtitle selection, subtitle styling, image settings, normalization, scaling, previews, cache budget, window state, fullscreen, cursor visibility, theme, language, and camera/microphone preferences remain local. The call provider transports meeting media independently. Membership, readiness, media identity, authorization, ordering, and timing messages support the protocol; they do not authorize additional synchronized application settings. Internal drift correction is not a shared user playback-speed preference.

## Player and input

The common PlayerChrome receives actual VLC observations and a native surface from the local WatchSession owner on all three platforms. Play/pause, seek, volume, mute, Library navigation, End, and fullscreen use that owner. The owner can attach an admitted shared session: play/pause and seek then enter the Syncplay coordinator synchronously, while volume and mute continue using the local player. The helper session closes before the native player is released. User-facing room/media admission, track/playlist/settings menus, complete input policies, and video overlays await their integrations.

Preserve the compact top controls, centered skip-back/play-pause/skip-forward controls, timeline, previous/next media, playlist, audio/subtitle menus, settings, library navigation, and call-window focus. Timeline previews use a separately owned decoder and bounded cache; they must not seek the movie, publish playback actions, or display canceled generations.

Fullscreen, minimize, return-to-library, and end-watching remain distinct actions. Popovers, context menus, controls, and previews must render above video and receive input after resize, display-scale changes, and fullscreen transitions.

| Input | Behavior |
|---|---|
| Left / Right | Skip by the local interval and publish the resulting position |
| Space | Toggle play/pause |
| Up / Down | Change movie volume by 5% |
| Mouse wheel | Change movie volume only |
| M | Toggle movie mute |
| F | Toggle fullscreen |
| Escape | Exit fullscreen |

Editable fields and call input retain their keys. Show transient movie-volume feedback without changing call volume.

While video plays and the pointer is idle over the video area, hide the cursor after the configured inactivity interval. Restore it on pointer movement, when paused, and while menus, controls, or editable overlays need interaction. Apply the behavior in windowed and fullscreen playback, only inside the player area.

## Audio and calling

One audio popover provides independent movie and call output controls. Movie mute, default/remembered movie volume, and mouse-wheel changes affect only movie output. Call volume controls received call audio independently of the movie, microphone, other applications, and peers.

Optional movie ducking is disabled by default. When remote-speaking detection is available, movie volume uses a 0.35 multiplier and returns to the user's level after a 280 ms release delay. System communications attenuation and other applications' volume are unaffected.

Use Google Meet through the room's **Open call** action in a SyncWatch-owned call component or separate owned window. SyncWatch must own its lifetime and local media teardown. Google supplies the meeting page and provider controls. The current implementation opens the operating system's HTTPS handler; it does not yet meet this owned-host requirement. CEF and bundled Chromium are candidates pending qualification.

With camera enabled by user preference and permission granted, joining starts the camera and preview on the first attempt, without an off/on workaround. Permission denial or unavailable hardware produces an explicit state. Minimizing an ongoing call does not end it; hangup or closing the call must stop participation, remote audio, and owned capture.

Outside an active joined call, no remote participant audio is audible. Connecting to a synchronization room does not implicitly join a call or open its audio path. After hangup, close, failed join, or call teardown, received call audio stops even if synchronization and movie playback continue.

The shared Design library owns call-window dimensions and SyncWatch controls. Native independent call volume, speaking-based ducking, preview, camera startup, and hangup are not implemented. Opening a URI is not joined state. The current external handoff cannot confirm local teardown; retain a clear route to leave in Meet until the owned host is qualified. See [Meet integration](architecture/google-meet.md) for capabilities and [Google setup](google-meet-setup.md) for registration.

## Settings

The application-settings dialog uses the shared 680 by 420 layout and 150-unit sidebar:

| Section | Required behavior |
|---|---|
| Library | Select/retain authorized folder access and configure automatic refresh |
| Interface | English/Russian localization and dark/light/system appearance without restart |
| Call | Input/output devices and supported camera quality; retain the 480p target subject to real provider capability |
| Connection | Synchronization/room connection and readiness behavior with independent channel states |

Theme and language are implemented in the native application-settings dialog and saved through the local state store, with feedback and validation errors. They apply without restart and survive application restart; Cancel discards unsaved edits. Library folder selection, access restoration, scanning, search and selection are connected to their local adapters on all three desktops. Local watch actions are enabled by the verified media runtime and current selection. Call and connection actions await their provider owners. Other supported configuration changes follow the same local-save boundary as their integrations are implemented. macOS folder grants use security-scoped bookmarks; moved, stale, denied, or corrupt grants need renewed folder access.

The player-settings dialog uses the shared 680 by 460 layout, 170-unit sidebar, draggable title bar, and section reset actions:

| Section | Required controls |
|---|---|
| Playback | Local skip interval, preview enablement, and supported playback behavior |
| Subtitles | Font, size, text/outline colors, outline width, padding, position, and vertical offset |
| Image | Brightness, contrast, saturation, and hue |
| Audio | Default/remembered movie volume and normalization |
| Advanced | Scaling, cursor behavior, and preview cache from 50 to 500 MB |

The PlayerSettings component is implemented in the native fixture galleries with draft editing, section reset, Save/Cancel and a subtitle text preview. Gallery saves acknowledge only in-memory fixture values. Applying or persisting these preferences in a real playback session depends on the selected VLC modules, media adapters and preview-service capabilities. Unsupported configuration remains unavailable until the caller grants those capabilities.

## Recovery and visual consistency

Playback synchronization, room metadata, and calling have independent connection states. Show participant presence/readiness and connecting, connected, reconnecting, interrupted, missing-media, and failed states where applicable. Synchronization loss pauses shared playback; a call-only failure leaves the movie running. Reconnect must not replay obsolete actions or create duplicate players, capture streams, or helpers.

All three desktop clients share design token IDs, component order, actions, and state contracts. The interface uses a two-column library, deep blue cinema-focused theme, compact dialogs, centered playback controls, timeline previews, audio popover, status indicators, and separate call window. The [architecture](architecture/README.md), [Syncplay integration](architecture/syncplay.md), and [wire contracts](architecture/contracts.md) describe ownership and ordering.

## Google account ownership

The three desktop shells bind `AccountController` to the same declarative `Account` component in SyncWatch Design. The account API adapter is isolated from room admission and playback, reusing only the service-origin and bounded JSON primitives in the native service packages. Account errors leave the library and local movie usable. No Google credential is bundled in an application or persisted in its preferences.

The account surface accepts a service origin, starts a proof-bound sign-in transaction, and explicitly opens its validated browser URI. Enter the browser's confirmation code in the initiating SyncWatch window. Choose **Check account**, then review and confirm the proposed account. The application owns session refresh, cancellation, and logout. Users can remember the device session through OS-protected storage or choose memory-only operation. Storage failure requires explicit recovery; it never permits plaintext fallback. The authorization browser's Google session remains separate from SyncWatch. Complete account-to-room-to-owned-call acceptance remains incomplete; see the [account service](account-service.md).
