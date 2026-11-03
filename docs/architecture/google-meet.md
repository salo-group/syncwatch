# Google Meet integration

The SyncWatch call architecture uses a separate application-owned window. The application manages its frame and lifecycle; a Google Meet surface handles the meeting. Call hosting is not implemented in the current native shells.

## Provider interfaces

| Interface or constraint | Architectural consequence |
|---|---|
| Meet REST API manages spaces and conference metadata | It can support meeting setup but does not supply a native two-way call renderer. [REST overview](https://developers.google.com/workspace/meet/api/guides/overview) |
| `spaces.create` creates a meeting space | Automated creation is optional after OAuth setup; accepting an existing Meet URL is an initial workflow. [API reference](https://developers.google.com/workspace/meet/api/reference/rest/v2/spaces/create) |
| Meet Media API is deprecated and its preview will not reach general availability | Google announced retirement on October 28, 2026, with endpoint shutdown on January 15, 2027. Do not adopt it for the calling baseline. The Meet REST API and Add-ons SDK remain supported, but do not supply a native two-way call renderer. [Release notes](https://developers.google.com/workspace/meet/release-notes), [Meet developer overview](https://developers.google.com/workspace/meet) |
| Meet add-ons run applications inside Meet | They do not establish that Meet can be embedded in a native application. [Add-ons overview](https://developers.google.com/workspace/meet/add-ons/guides/overview) |
| Google lists supported browsers | Embedded hosts still require their own feasibility test. [Meet requirements](https://support.google.com/meet/answer/7317473?hl=en) |
| OAuth restricts embedded user agents | API authorization uses a supported external-user-agent flow. An API token does not automatically establish a Meet web session. [Native OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [embedded webview policy](https://developers.googleblog.com/upcoming-security-changes-to-googles-oauth-20-authorization-endpoint-in-embedded-webviews/) |

These interfaces do not supply a complete native call-control contract. Successful navigation alone does not establish call participation or native control support.

## Call window host

The following hosts are candidates, not implemented calling support. Provider status was checked against the official sources above on October 31, 2026.

| Desktop | Native owner and host boundary |
| --- | --- |
| macOS | Swift/AppKit owns the `NSWindow`; top-level WKWebView navigation is a feasibility candidate. |
| Windows | C#/WPF owns the window; CEF/CefSharp is an open-source feasibility candidate. |
| Linux | Kotlin owns the desktop call window. Its embedded host remains to be selected and qualified on Linux; a Windows host or macOS check cannot supply this evidence. |

Browser-host compatibility remains unresolved. Any adopted external host and its complete [source dependency graph](dependencies.md) must be pinned under `3rdparty/`. Before investing in a browser SDK, establish a supported account or guest path, actual two-way media and complete shutdown in the intended host. A REST authorization token does not establish a Meet web session, and restricted embedded OAuth must not be bypassed by user-agent changes or cookie transfer.

The native shell owns move, resize, pin, minimize, restore, fullscreen, and failure presentation. The content area displays Google Meet. Do not depend on iframe embedding, user-agent spoofing, imported browser cookies, or unstable DOM selectors for production controls.

Provider content receives only the permissions necessary for the call. Do not expose native file access, playback control, or room credentials to the provider page. Use an application-specific browser profile and constrain privileged navigation. Handle account access failures explicitly; API authorization alone does not solve web authentication.

## State and capabilities

The host can represent `idle`, `opening`, `webSurfaceReady`, `userActionRequired`, `failed`, and `closed`. Represent joined state, camera/microphone state, remote video availability, and speaking activity only when the integration provides a reliable signal.

| Call behavior | Host responsibility |
|---|---|
| Open and restore the call window | Native window lifecycle independent of meeting navigation |
| Two-way camera and microphone | Provider participation and device capture |
| Device selection and native media controls | Available provider interfaces |
| Hangup and close | Capture and participation stop, including when the window closes |
| Remote video | A usable video presentation within the application-owned window |
| Local camera thumbnail | Compact actual self-view alongside remote video, with active, disabled, denied, and unavailable states |
| Initial camera state | Enabled camera starts on first join with permission granted, without an off/on workaround |
| Independent call volume | Zero/mute, intermediate, and full values affect only received call output; movie, microphone, other applications, and peers remain unchanged |
| Inactive call audio | No received audio before join or after hangup, close, failed join, or teardown; synchronization-room membership alone does not open audio |
| Remote-speaking notification | A reliable signal for movie ducking |
| Camera quality | Actual selectable provider/host capability |
| Compact call layout | A usable minimum viewport with functioning meeting controls |

Capabilities such as `canObserveJoin`, `canMuteFromNativeUI`, `canSelectDevicesFromNativeUI`, `canSetCallVolume`, `canObserveRemoteSpeaking`, and `canConfirmHangup` default to false. Only a reliable provider integration can advertise support.

The existing six-field capability schema has no local-preview or initial-camera fields. Native actions for these features need an explicit versioned capability boundary. A loaded page or open audio path is not equivalent to a joined call.

## Known limitations

All three desktop hosts remain unqualified candidates. Embedded account access, guest participation, two-way media, native controls, independent call volume, and reliable hangup remain unresolved. Google-supported browser compatibility does not automatically extend to embedded WebKit or CEF hosts.
