# Google sign-in and Meet setup

SyncWatch needs one developer-managed Google integration for accounts and meeting creation. Participants sign in through Google; they do not create Cloud projects or enter API credentials. All three desktop clients provide account sign-in, authenticated rooms and a room-owned Meet flow. Real calling acceptance remains incomplete; configuration alone does not qualify a deployment.

The [cost policy](architecture/cost-policy.md) requires ordinary free Google accounts and no additional paid services. Verify meeting creation for those accounts; an organizer's paid Workspace subscription or trial is not an acceptance substitute.

## Development registration

Use an owner-controlled Google account to create a dedicated **SyncWatch Development** project in [Google Cloud Console](https://console.cloud.google.com/projectcreate). Keep production in a separate project. The owner handles account terms and organizational requirements. Do not enable billing or purchase services merely to complete setup; any billing prompt needs an explicit deployment decision.

Enable **Google Meet REST API**. Configure Google Auth Platform as follows:

| Setting | Development value |
| --- | --- |
| App name | `SyncWatch` |
| Support and developer contacts | A reachable owner-controlled account |
| Audience | External, unless the product is intentionally organization-only |
| Publishing status | Testing |
| Test users | Only consenting development accounts |
| Identity scopes | `openid`, `profile`, `email` |
| Additional organizer scope | `https://www.googleapis.com/auth/meetings.space.created` |
| OAuth client | Web application, named `SyncWatch Development Backend` |
| Development callback | Exact backend callback; local example: `http://127.0.0.1:8788/auth/google/callback` |

This callback belongs to the optional confidential [account service](account-service.md). The same registered callback handles additional [Meet authorization](meet-service.md); a Web client secret never belongs in a desktop application. Google permits localhost HTTP callbacks for development. Public callbacks require an exact HTTPS URI on an authorized domain. [Web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

Create the OAuth client through the Console and retain its secret outside Git in the backend's protected secret store. Do not paste secrets into chat, logs, preferences, screenshots, or metadata. Client ID is public configuration; client secret and Google refresh tokens stay server-side. [OAuth best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices).

Request basic identity at sign-in and the additional Meet scope only when an organizer starts a call. Check actual granted scopes and Google subject. Ordinary participants do not need permission to create meetings. The Meet scope is Sensitive and requires applicable verification before public distribution. Test grants can expire; reauthorization is a required workflow. [Meet authorization](https://developers.google.com/workspace/meet/api/guides/authenticate-authorize).

Development does not require buying a domain first. Remote acceptance needs deliberate backend/callback routing on each desktop. Never expose a creation key, Web secret, Google token, or plaintext non-loopback service to make a local callback reachable. Cloud setup alone does not qualify sign-in or calls.

## Desktop workflow

Sign in through **Google account** using the configured account service. Open the browser explicitly, finish Google sign-in there, and enter the SyncWatch confirmation code only in the initiating desktop client. Check the verified profile before confirming the account. SyncWatch saves its own session in the device's secure storage; Google browser cookies remain separate.

Create or join an authenticated room, then choose **Open call** in Library or Player. The organizer selects **Allow SyncWatch to create calls**, completes the separate browser permission request with the same Google account, verifies its new SyncWatch confirmation code and confirms **Allow calls for this account**. Guests do not grant organizer access. An expired request cannot be confirmed; start a fresh request through the same Allow action. Room membership and movie playback remain available.

After permission is confirmed, the organizer chooses **Start room call**. Admitted members choose **Open in Google Meet**, then select devices and join in Meet. **End call for everyone** requires a separate organizer confirmation. Closing the SyncWatch call panel leaves the external browser or PWA under its own control.

## Browser and PWA

Use a current supported browser. The official Meet PWA offers a separate app-like window on macOS, Windows, and Linux through Chrome. Installation is an explicit user action in Google's interface; SyncWatch does not package Chrome or install the PWA silently. A meeting may first open in a browser tab and offer **Open app**. Account selection and media permissions remain in that user agent. [Meet PWA](https://support.google.com/meet/answer/10708569?hl=en).

Browser and SyncWatch accounts have separate sessions. Test account switching and Workspace restrictions; never copy cookies or inject OAuth tokens. A browser-open acknowledgement is not joined media or active capture.

## Public deployment

Before enabling public accounts, configure owned HTTPS hosting/callback, verified homepage, privacy policy, terms, reachable contacts, production OAuth project, applicable verification, quotas, secure key/token storage, deletion/retention, and backup/restore. Hosting, domains, partner services, and subscriptions are separate operator decisions; development registration implies no purchase. [OAuth policy](https://developers.google.com/identity/protocols/oauth2/policies).

Follow the [account/room architecture](architecture/accounts-and-rooms.md) and [Meet integration](architecture/google-meet.md). Readiness needs observed account, room, call, failure, restart, and cleanup behavior on all three desktops.
