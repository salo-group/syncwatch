# Desktop application acceptance

Each feature is implemented and checked together on macOS, Windows and Linux.
Run the actual native application in a visible desktop session on each target.
Check its input, focus, window sizing, shared design, unavailable states and
resource release. Build and offscreen rendering results are separate evidence.

Synchronization uses an authenticated Syncplay room. Three clients require six
directed sender/observer cases, even when they share the same room concurrently:

| Sender | Observer |
|---|---|
| macOS | Windows |
| Windows | macOS |
| macOS | Linux |
| Linux | macOS |
| Windows | Linux |
| Linux | Windows |

For each direction, initiate play, pause and seek through the sender's visible
controls. Observe the receiver's real VLC state, position and native output;
compare positions with the synchronization contract's tolerance. A requested
state or transport acknowledgement is insufficient. Check late join, reconnect,
media mismatch, stale generations and closing a participating window. Keep local
volume and track changes local, and check that observations do not echo remote
commands as new user actions.

Record each case against the exact source, dependency and artifact identities,
with its desktop observation and result. A missing client implementation, failed
case or missing receiver observation leaves the feature incomplete. Preserve
failures alongside corrected results. CI exercises the common contracts and
available automated native checks; it does not imply this desktop acceptance.

The explicit application check `--watch-check-mode shared-session` attaches an
owned helper to an authored movie selected through Library. It requires the
existing watch-check media/report/state-directory arguments and
`--shared-check-config` pointing to an authored configuration. The configuration
provides absolute `runtime`, `executable`, `control` and `state` paths, plus the
TLS `host`, `port`, `room` and `username`. Its `expectedMedia` object contains
`mediaId`, `title`, `titleKey`, `fileSizeBytes`, `durationMs` and
`fullHashSha256`. The native owner hashes the selected file, checks observed
duration, and requires an unambiguous full-content match before launching its
helper. Weak/missing/ambiguous media cannot connect through this check.
This fixture admission is separate from
normal room/media admission and does not qualify the application for deployment.

The state file contains actual VLC observations and authenticated session state.
Optional sequenced control instructions dispatch the same player-controller
actions as the visible controls; `finish` closes the owned watch session. Check
the final report and process exit independently. Automated controller dispatch
and interactive control checks are separate evidence. Observation files are
replaced atomically; Windows readers must share deletion while reading them so
they cannot prevent replacement. Keep test trust-store derivatives separate from
the immutable runtime and retain their original receipts and changed-file hashes.

## Authenticated recovery regression

The opt-in macOS test `authenticatedPlaybackSurvivesShortNetworkOutage` drives the normal room, VLC and helper owners.
It preserves membership and watch generation, then checks the next metadata command.
Use synthetic accounts from the Windows `RoomMetadata.Tests --native-account-room-fixture` host.
That host requires an owned admission runtime through `SYNCWATCH_ADMISSION_RUNTIME`.
Keep its test CA in a separate, inventoried helper derivative.

Set `SYNCWATCH_RECOVERY_PROBE` to a private JSON file with these fields:

| Field | Value |
| --- | --- |
| `owner` | Saved-session object from the fixture's `macos.json` file |
| `state` | Absolute path for disposable local preferences |
| `media` | Absolute path to the qualified Media package |
| `helper` | Absolute path to the test helper package |
| `folder` | Absolute folder containing the authored `SyncWatch Shared Watch.avi` fixture |
| `drop` | Absolute path where the test requests an interruption |
| `restored` | Absolute path for the harness restoration receipt |

The harness monitors `drop` and interrupts only its owned API and Syncplay forwards for five seconds.
After restoration, it writes `restored` with numeric `actualOutageSeconds`, measured with a monotonic clock.
The regression requires an elapsed interruption from five seconds to less than ten seconds.

Run this command on macOS:

```sh
swift test --package-path apps/macos --filter authenticatedPlaybackSurvivesShortNetworkOutage
```

The harness must close its backend, forwards and helper derivatives after success, failure or timeout.
Visible application checks on all three systems and six directed controls remain separate acceptance requirements.
