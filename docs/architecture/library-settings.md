# Movie-folder selection in Settings

The common Apache-2.0 Design package defines the folder row, its observed path and the Choose folder action. Desktop hosts supply the current path and observed availability; no folder permission, filesystem operation or platform layout belongs in Design. The row cannot convert an arbitrary text edit into a filesystem grant.

Library and Settings use the same native chooser and LibraryController. Selecting a folder immediately starts the existing scan and persisted-access workflow. Cancelling the chooser changes nothing. The displayed path updates when that owner publishes a selection; scanning or closed ownership disables another choice. Scan and persistence failures use the existing library error flow. A restored selection supplies the same path on the next application launch.

The folder path is an observation, separate from the editable Settings draft. It does not consume a text-field length budget or get written into appearance preferences. Theme, language and library-option edits survive folder updates. Save acknowledges those editable settings; Cancel discards their unsaved draft and does not reverse an already accepted native folder selection. Unsupported local-state versions keep the Settings chooser read-only.

Each platform attaches the chooser to its Settings host when available and retains the library module's access owner through scanning and playback. macOS uses security-scoped access; Windows and Linux recheck their existing filesystem access. Complete window interaction and restart acceptance require native GUI verification on each OS.
