# Local playback preferences

The player opens the common Apache-2.0 Design settings dialog from its settings action. The native application enables the skip interval, default movie volume and remembered movie volume. Field definitions, choices, labels, themes, geometry and validation belong to Design; unavailable playback features remain disabled.

Each open dialog starts with a fresh local draft. Save validates the supported choice and writes the complete local-state snapshot before publishing a changed interval. A write failure leaves the applied interval unchanged and shows the shared failure state. Cancel or closing the dialog discards unsaved changes. Reset affects only supported fields in the selected section.

The saved interval is applied to the current player and subsequent sessions. Keyboard and button skip actions still produce an ordinary bounded seek through the existing player presentation. In a shared watch, that seek goes through the existing admitted Syncplay session. Editing or saving preferences does not publish a seek, change the playback position, or change another participant's preferences.

The existing local-state owner serializes writes and retains unrelated preferences, selection and resume positions. A stale watch checkpoint merges the latest saved preferences. A checkpoint already writing makes preference saves unavailable; unsupported state versions remain read-only. Older valid intervals outside the current Design choices are retained until a supported interval is explicitly saved.

Native component and persistence checks do not replace actual settings-window, keyboard/button, six-direction shared-watch and same-package restart acceptance on macOS, Windows and Linux.

Default movie volume is an optional normalized field in version1 local state. Its absence in an older file resolves through the common Design default; the stored remembered movie volume is preserved. A new playback scope applies remembered volume when enabled, otherwise the configured default, before readiness, shared admission and autoplay. Mute remains transient.

Only a successfully acknowledged local volume command can update remembered volume. The owning watch coordinator rejects callbacks from an old scope and coalesces accepted changes through its existing serialized checkpoint. Room commands do not carry volume. Turning remembering off retains the last saved value while subsequent scopes use the configured default. Changing default volume affects subsequent scopes.
