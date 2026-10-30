import SwiftUI

/// Presentation facts and input translation only. Layout, typography and visual variants live in layouts.json.
enum DesignBindings {
    static func capabilities(_ actions: [String], allows: (String) -> Bool) -> [String: String] {
        Dictionary(uniqueKeysWithValues: actions.map { ("can." + $0, String(allows($0))) })
    }
    static func library(_ p: LibraryPresentation, query: String, language: String) -> DesignContext {
        var values = capabilities(SyncWatchCopy.libraryActions, allows: p.allows)
        values.merge([
            "namespace": "library", "query": query, "entryCount": String(p.entries.count),
            "filesLabel": SyncWatchCopy.text("files", language: language), "hasEntries": String(!p.entries.isEmpty),
            "hasSelection": String(p.selected != nil), "selectedTitle": p.selected?.title ?? "",
            "selectedLocation": p.selected?.location ?? "",
            "emptyMessage": p.state == .scanning ? "scanning" : "empty",
            "missingMedia": String(p.state == .missingMedia),
            "roomMessage": !p.roomStatus.isEmpty
                ? p.roomStatus : p.state == .reconnecting ? "reconnecting" : "disconnected",
        ]) { _, v in v }
        let unknown = SyncWatchCopy.text("unknown", language: language)
        let duration = p.metadataDurationMs.flatMap { ms -> String? in
            guard ms > 0 else { return nil }
            return SyncWatchCopy.text("metadataDurationFormat", language: language)
                .replacingOccurrences(of: "{hours}", with: String(ms / 3_600_000))
                .replacingOccurrences(of: "{minutes}", with: String(format: "%02d", Int(ms / 60_000 % 60)))
                .replacingOccurrences(of: "{seconds}", with: String(format: "%02d", Int(ms / 1_000 % 60)))
        }
        values["metadataDuration"] = duration ?? unknown
        if p.metadataWidth > 0 && p.metadataHeight > 0 {
            values["metadataResolution"] = SyncWatchCopy.text("metadataResolutionFormat", language: language)
                .replacingOccurrences(of: "{width}", with: String(p.metadataWidth))
                .replacingOccurrences(of: "{height}", with: String(p.metadataHeight))
        } else {
            values["metadataResolution"] = unknown
        }
        values["metadataAudio"] =
            p.metadataAudioName.map { $0.isEmpty ? unknown : $0 }
            ?? p.metadataAudioTracks.map(String.init) ?? unknown
        if p.metadataSubtitlesDisabled || p.metadataSubtitleTracks == 0 {
            values["metadataSubtitles"] = SyncWatchCopy.text("metadataNoSubtitles", language: language)
        } else {
            values["metadataSubtitles"] =
                p.metadataSubtitleName.map { $0.isEmpty ? unknown : $0 }
                ?? p.metadataSubtitleTracks.map(String.init) ?? unknown
        }
        values["metadataLoading"] = String(p.metadataLoading)
        values["metadataUnavailable"] = String(p.metadataUnavailable)
        let entries = p.entries.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) }.map {
            DesignContext([
                "id": $0.id, "title": $0.title, "selected": String($0.id == p.selectedID),
                "folderMovie": String($0.folderMovie),
                "rowDuration": $0.durationMs.flatMap { ms in
                    ms > 0
                        ? SyncWatchCopy.text("libraryRowDurationFormat", language: language)
                            .replacingOccurrences(of: "{hours}", with: String(ms / 3_600_000))
                            .replacingOccurrences(of: "{minutes}", with: String(format: "%02d", Int(ms / 60_000 % 60)))
                        : nil
                } ?? "",
            ])
        }
        return DesignContext(values, collections: ["entries": entries])
    }
    static func player(_ p: PlayerPresentation) -> DesignContext {
        var values = capabilities(SyncWatchPlayer.actions, allows: p.allows)
        values.merge([
            "namespace": "player", "title": p.title, "state": p.state.rawValue,
            "hasSharedPosition": String(p.allows("dismissSharedPosition")),
            "sharedPositionText": PlayerPresentation.time(Double(p.sharedPositionMs ?? 0)),
            "roomNotice": p.roomNotice, "hasRoomNotice": String(!p.roomNotice.isEmpty),
            "position": String(p.displayedPosition), "duration": String(p.displayedDuration),
            "positionText": PlayerPresentation.time(p.displayedPosition),
            "durationText": PlayerPresentation.time(p.displayedDuration),
            "volume": String(p.isValid ? p.volume : 0), "playIcon": p.playing ? "player.pause" : "player.play",
            "playLabel": p.playing ? "pause" : "play", "volumeIcon": p.muted ? "player.muted" : "player.volume",
            "volumeLabel": p.muted ? "unmuteMovie" : "muteMovie",
        ]) { _, v in v }
        return DesignContext(values)
    }
    static func settings(_ editor: SettingsEditor, section: String) -> DesignContext {
        let fields = SyncWatchSettings.fields.filter { $0.section == section }.map { field in
            let options = field.options.map { option in
                DesignContext(["id": option, "label": "option" + option.prefix(1).uppercased() + option.dropFirst()])
            }
            return DesignContext(
                [
                    "id": field.id, "label": field.label,
                    "kind": ["choice", "boolean"].contains(field.kind) ? field.kind : "text",
                    "value": editor.values[field.id] ?? "",
                    "editable": String(editor.editableFields.contains(field.id)), "isRange": "false",
                ], collections: ["options": options])
        }
        let status =
            !editor.canSave ? "settingsReadOnly" : editor.state == .savedNotice ? "changesSaved" : editor.message
        return DesignContext(
            [
                "namespace": "settings", "section": section, "dialogTitle": "openSettings", "status": status,
                "saved": String(editor.state == .savedNotice), "can.save": String(editor.canSave), "can.close": "true",
                "can.cancel": "true",
                "interfaceSection": String(section == "interface"),
                "unavailable": String(
                    !SyncWatchSettings.fields.contains {
                        $0.section == section && editor.editableFields.contains($0.id)
                    }),
            ], collections: ["sections": sections(SyncWatchSettings.sections, selected: section), "fields": fields])
    }
    static func playerSettings(_ editor: PlayerSettingsEditor, section: String, language: String) -> DesignContext {
        let fields = SyncWatchPlayerSettings.fields.filter { $0.section == section }
        let rows = Set(fields.map(\.row)).sorted().map { row in
            DesignContext(
                ["id": String(row)],
                collections: [
                    "fields": fields.filter { $0.row == row }.map { field in
                        let raw = editor.values[field.id] ?? ""
                        let safe =
                            PlayerSettingsEditor.valid(raw, for: field)
                            ? raw : SyncWatchPlayerSettings.defaults[field.id] ?? ""
                        let options = field.options.map { option in
                            DesignContext([
                                "id": option.id, "fieldID": field.id, "label": option.label,
                                "selected": String(option.id == raw),
                                "swatchColor": option.colorToken.map { "@" + $0 } ?? "",
                            ])
                        }
                        return DesignContext(
                            [
                                "id": field.id, "label": field.label, "kind": field.kind, "value": raw,
                                "number": safe, "minimum": String(field.minimum), "maximum": String(field.maximum),
                                "step": String(field.step),
                                "unitText": field.unit == "unitNone"
                                    ? "" : " " + SyncWatchCopy.text(field.unit, language: language),
                                "isRange": String(field.kind == "range"), "editable": String(editor.allows(field.id)),
                            ], collections: ["options": options])
                    }
                ])
        }
        var context = preview(editor.values)
        context.values.merge([
            "namespace": "playerSettings", "section": section, "dialogTitle": "playerSettings",
            "status": !editor.canSave ? "settingsReadOnly" : editor.message,
            "saved": String(editor.state == .savedNotice), "can.save": String(editor.canSave),
            "can.resetSection": String(editor.canReset(section)),
            "can.close": "true", "can.cancel": "true", "showSubtitle": String(section == "subtitles"),
        ]) { _, v in v }
        context.collections = ["sections": sections(SyncWatchPlayerSettings.sections, selected: section), "rows": rows]
        return context
    }
    static func preview(_ values: [String: String]) -> DesignContext {
        func setting(_ id: String) -> String {
            guard let field = SyncWatchPlayerSettings.fields.first(where: { $0.id == id }), let value = values[id],
                PlayerSettingsEditor.valid(value, for: field)
            else { return SyncWatchPlayerSettings.defaults[id] ?? "" }
            return value
        }
        var result = Dictionary(
            uniqueKeysWithValues: ["subtitleSize", "subtitlePadding", "subtitleOffset", "subtitleFont", "outlineWidth"]
                .map { ($0, setting($0)) })
        result["subtitleTop"] = String(setting("subtitlePosition") == "top")
        for (binding, id) in [("subtitleFill", "subtitleColor"), ("outlineFill", "outlineColor")] {
            let option = SyncWatchPlayerSettings.fields.first { $0.id == id }?.options.first { $0.id == setting(id) }
            result[binding] = option?.colorToken.map { "@" + $0 } ?? ""
        }
        return DesignContext(result)
    }
    private static func sections(_ source: [String], selected: String) -> [DesignContext] {
        source.map { DesignContext(["id": $0, "selected": String($0 == selected)]) }
    }
    static func call(_ p: CallPresentation, hasRemote: Bool, hasLocal: Bool) -> DesignContext {
        var values = [
            "showRemote": String(p.showsRemoteSurface && hasRemote),
            "showLocal": String(p.showsLocalPreview && hasLocal),
            "previewLabel": p.previewLabel(hasSurface: hasLocal),
            "callStateLabel": "callState" + p.state.rawValue.prefix(1).uppercased() + p.state.rawValue.dropFirst(),
            "maximumExtent": String(SyncWatchCall.maximumExtent), "volume": String(p.displayedVolume),
            "pinned": String(p.pinned), "fullscreen": String(p.fullscreen), "minimized": String(p.minimized),
            "microphoneEnabled": String(p.microphoneEnabled == true), "cameraEnabled": String(p.cameraEnabled == true),
            "microphoneOff": String(p.microphoneEnabled == false), "cameraOff": String(p.cameraEnabled == false),
            "nextMicrophone": p.microphoneEnabled == true ? "0" : "1",
            "nextCamera": p.cameraEnabled == true ? "0" : "1",
            "pinLabel": p.pinned ? "unpinCall" : "pinCall",
            "fullscreenLabel": p.fullscreen ? "exitFullscreenCall" : "fullscreenCall",
            "fullscreenIcon": p.fullscreen ? "call.restore" : "call.fullscreen",
            "volumeIcon": p.displayedVolume > 0 ? "call.volume" : "call.muted",
            "microphoneLabel": p.microphoneEnabled == true ? "microphoneOn" : "microphoneOff",
            "cameraLabel": p.cameraEnabled == true ? "cameraOn" : "cameraOff",
        ]
        for action in SyncWatchCall.actions { values["can." + action] = String(p.allows(action)) }
        return DesignContext(values)
    }
}
