# Google Meet integration

## Selected calling mode

Google Meet remains the call provider. SyncWatch owns Google sign-in, room membership, meeting creation, invitations, and the action that opens the room's meeting. Meet runs in a supported browser or its official Progressive Web App (PWA). Prefer the PWA experience where available so the call has its own window; retain the supported-browser path. This is a separate Google-managed window, not a native surface embedded in SyncWatch.

Google documents the Meet PWA for macOS, Windows, and Linux. It requires Chrome and has the same Meet features as the web client. Opening a meeting in the PWA may require the browser's **Open app** action. Do not assume an undocumented launch flag or a universal PWA deep-link API. [Meet PWA](https://support.google.com/meet/answer/10708569?hl=en).

This is the selected target, not a delivered call implementation. Authenticated permission/provisioning APIs and their three native ports are implemented; native window integration, complete room-lifecycle cleanup and real calling acceptance remain incomplete. See the [Meet service](../meet-service.md), [account and room architecture](accounts-and-rooms.md) and [Google setup](../google-meet-setup.md).

## User experience

1. Sign in to SyncWatch through Google and join an authorized room.
2. The organizer requests a call and grants the narrow Meet permission when needed. The server creates one associated space and publishes its URI to admitted members.
3. **Open call** in Library or Player opens that room's canonical URI through the supported HTTPS handler. Confirm the account, devices, permissions, and **Join** in Meet.
4. Movie playback continues in SyncWatch. Camera/microphone, local preview, device selection, and local hangup are controlled in Meet. A call failure does not stop the movie.
5. An organizer-only **End call for everyone** can use the backend's authorized REST operation once implemented. It is never a substitute for a participant's local hangup.

Keep the application design and add account/call-entry state through the common Apache-2.0 Design library. Google's meeting surface and window chrome belong to Google. Do not duplicate their controls unless a reliable integration can perform and observe the corresponding operation.

Closing SyncWatch, leaving its room, or revoking its device session does not prove that an external Meet window stopped capture/audio. Explain this and provide a route back to Meet. Closing/ending in Meet must be tested separately. Never terminate a shared browser process, close unrelated windows, reparent the browser, inject scripts/cookies, or request Accessibility permissions to impersonate native ownership.

The browser's Google account is independent of SyncWatch OAuth. Successful app sign-in does not sign Meet in automatically. Make account selection/admission in Meet explicit; a guest display name is not verified room identity.

## Provider interfaces

| Interface | Consequence |
| --- | --- |
| Meet REST manages spaces and conferences | Supports provisioning, not a native renderer or control of a participant's browser. [Overview](https://developers.google.com/workspace/meet/api/guides/overview) |
| `spaces.create` | Backend uses the organizer's explicit grant; only admitted members receive the result. [Create](https://developers.google.com/workspace/meet/api/reference/rest/v2/spaces/create) |
| `spaces.endActiveConference` | A global organizer action; does not delete the space, permanently revoke its URI, or close browser windows. [End](https://developers.google.com/workspace/meet/api/reference/rest/v2/spaces/endActiveConference) |
| OAuth prohibits developer-controlled embedded user agents | Use a supported authorization flow without importing tokens/cookies into a WebView. [Policy](https://developers.google.com/identity/protocols/oauth2/policies) |
| Supported browsers and PWA | Qualify actual handler and meeting behavior on all three OSes. [Requirements](https://support.google.com/meet/answer/7317473?hl=en) |
| Add-ons embed applications inside Meet | Do not implement Meet inside a native app. [Add-ons](https://developers.google.com/workspace/meet/add-ons/guides/overview) |
| Meet Media API is discontinued | New enrollment is closed; endpoints shut down January 15, 2027. Do not adopt it. [Developer overview](https://developers.google.com/workspace/meet) |

The installed browser/PWA is an external user agent, not a bundled runtime or application library. SyncWatch does not distribute or silently install it. Adopted libraries still require pinned Git submodules and complete source/license closure under `3rdparty/`. No CEF, CefSharp, WebKitGTK, or private Embed SDK is selected for this mode.

## Desktop adapters

Swift/macOS, C#/Windows, and Kotlin/Linux validate the admitted URI and request the registered HTTPS handler through their native platform boundary. Do not execute server-supplied commands or browser argument strings. The browser/PWA owns its window, account, devices, preview, capture, and hangup.

Accept only a canonical HTTPS `meet.google.com` meeting URI from the current authenticated room. Reject userinfo, nondefault ports, query/fragment data, malformed meeting codes, foreign hosts, arbitrary schemes, stale epochs, and actions after leave/cancellation. Never log the URI. A missing/failed handler produces a common localized retry action; retry opens the same space without creating another meeting.

Opening is explicit, never a side effect of join, reconnect, snapshot refresh, or restart. Single-flight requests prevent rapid duplicate launches. Invalidate pending work on meeting/room replacement, leave, logout, and close. A late completion cannot reactivate an obsolete flow.

## State and capabilities

Proposed handoff states are `unavailable`, `available`, `opening`, `handedOff`, and `failed`, bound to room epoch and meeting identity. `handedOff` means the OS accepted an open request; it does not prove a visible window, joined participant, or active capture. Version these proposed contracts before implementation; they are not new values in the existing v1 call-host schema.

`canObserveJoin`, `canMuteFromNativeUI`, `canSelectDevicesFromNativeUI`, `canSetCallVolume`, `canObserveRemoteSpeaking`, and `canConfirmHangup` remain false in ordinary browser/PWA mode. Conference-level REST metadata is not this device's joined state. Do not persist fake camera/microphone state or acknowledge a native mute/hangup request that merely changes a label.

Independent received-call gain in SyncWatch, remote-speaking ducking, automatic local browser hangup, native self-view, app-controlled pin/size/layout, and a shared Google web session remain unmet capabilities. Selecting external Meet does not implicitly satisfy or remove those requirements. Existing native call-shell galleries are design references, not this mode's production call surface.

## Acceptance

Deliver the same complete room-to-call journey on all three desktops. Verify browser handoff and the PWA path where available: Library/Player entry, no handler, wrong account, permission denial, duplicate clicks, stale/replaced meetings, leave/logout/close during opening, reopen, and independent movie playback. Actual two-way calling needs all six directed desktop audio/video cases with visible Google controls; source/fixture checks do not establish it.

Record actual Join/Leave, device/capture outcomes, and browser ownership separately from SyncWatch state. Clean up only test-created meetings, windows, profiles, and resources. Preserve unsupported and unverified capability boundaries.
