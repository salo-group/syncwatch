# Local playback preferences

The player opens the common Apache-2.0 Design settings dialog from its settings action. The native application currently enables the skip interval. Field definitions, choices, labels, themes, geometry and validation belong to Design; unavailable playback features remain disabled.

Each open dialog starts with a fresh local draft. Save validates the supported choice and writes the complete local-state snapshot before publishing a changed interval. A write failure leaves the applied interval unchanged and shows the shared failure state. Cancel or closing the dialog discards unsaved changes. Reset affects only supported fields in the selected section.

The saved interval is applied to the current player and subsequent sessions. Keyboard and button skip actions still produce an ordinary bounded seek through the existing player presentation. In a shared watch, that seek goes through the existing admitted Syncplay session. Editing or saving preferences does not publish a seek, change the playback position, or change another participant's preferences.

The existing local-state owner serializes writes and retains unrelated preferences, selection and resume positions. A stale watch checkpoint merges the latest saved preferences. A checkpoint already writing makes preference saves unavailable; unsupported state versions remain read-only. Older valid intervals outside the current Design choices are retained until a supported interval is explicitly saved.

Native component and persistence checks do not replace actual settings-window, keyboard/button, six-direction shared-watch and same-package restart acceptance on macOS, Windows and Linux.
