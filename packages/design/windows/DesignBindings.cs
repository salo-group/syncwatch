using System.Globalization;

namespace SyncWatch.Design;

// Observation/model lowering only. No colors, geometry, typography or native widget creation.
internal static class DesignBindings
{
    private static string Flag(bool value) => value ? "true" : "false";
    private static string Number(double value) => value.ToString(CultureInfo.InvariantCulture);
    private static Dictionary<string, string> Capabilities(IEnumerable<string> actions, Func<string, bool> allows) => actions.ToDictionary(a => "can." + a, a => Flag(allows(a)));
    private static DesignContext[] Sections(IEnumerable<string> ids, string selected) => ids.Select(id => new DesignContext { Values = new() { ["id"] = id, ["selected"] = Flag(id == selected) } }).ToArray();
    public static DesignContext Library(LibraryPresentation p, string query, string language)
    {
        var v = Capabilities(SyncWatchCopy.LibraryActions, p.Allows);
        foreach (var (key, value) in new Dictionary<string, string> { ["namespace"] = "library", ["query"] = query, ["entryCount"] = p.Entries.Count.ToString(CultureInfo.InvariantCulture), ["filesLabel"] = SyncWatchCopy.Text("files", language), ["hasEntries"] = Flag(p.Entries.Count > 0), ["hasSelection"] = Flag(p.Selected is not null), ["selectedTitle"] = p.Selected?.Title ?? "", ["selectedLocation"] = p.Selected?.Location ?? "", ["emptyMessage"] = p.State == LibraryVisualState.Scanning ? "scanning" : "empty", ["missingMedia"] = Flag(p.State == LibraryVisualState.MissingMedia), ["roomMessage"] = p.RoomStatus.Length > 0 ? p.RoomStatus : p.State == LibraryVisualState.Reconnecting ? "reconnecting" : "disconnected" }) v[key] = value;
        string unknown = SyncWatchCopy.Text("unknown", language);
        v["metadataDuration"] = p.MetadataDurationMs is long ms && ms > 0
            ? SyncWatchCopy.Text("metadataDurationFormat", language).Replace("{hours}", (ms / 3_600_000).ToString(CultureInfo.InvariantCulture))
                .Replace("{minutes}", (ms / 60_000 % 60).ToString("D2", CultureInfo.InvariantCulture))
                .Replace("{seconds}", (ms / 1_000 % 60).ToString("D2", CultureInfo.InvariantCulture)) : unknown;
        v["metadataResolution"] = p.MetadataWidth > 0 && p.MetadataHeight > 0
            ? SyncWatchCopy.Text("metadataResolutionFormat", language).Replace("{width}", Number(p.MetadataWidth)).Replace("{height}", Number(p.MetadataHeight)) : unknown;
        v["metadataAudio"] = p.MetadataAudioName is { } audio ? (audio.Length == 0 ? unknown : audio) : p.MetadataAudioTracks?.ToString(CultureInfo.InvariantCulture) ?? unknown;
        v["metadataSubtitles"] = p.MetadataSubtitlesDisabled || p.MetadataSubtitleTracks == 0 ? SyncWatchCopy.Text("metadataNoSubtitles", language) : p.MetadataSubtitleName is { } subtitles ? (subtitles.Length == 0 ? unknown : subtitles) : p.MetadataSubtitleTracks?.ToString(CultureInfo.InvariantCulture) ?? unknown;
        v["metadataLoading"] = Flag(p.MetadataLoading); v["metadataUnavailable"] = Flag(p.MetadataUnavailable);
        return new()
        {
            Values = v,
            Collections = new()
            {
                ["entries"] = p.Entries.Where(e => query.Length == 0 || e.Title.Contains(query, StringComparison.CurrentCultureIgnoreCase)).Select(e => new DesignContext
                {
                    Values = new()
                    {
                        ["id"] = e.Id,
                        ["title"] = e.Title,
                        ["folderMovie"] = Flag(e.FolderMovie),
                        ["selected"] = Flag(e.Id == p.SelectedId),
                        ["rowDuration"] = e.DurationMs is long duration && duration > 0
                ? SyncWatchCopy.Text("libraryRowDurationFormat", language).Replace("{hours}", (duration / 3_600_000).ToString(CultureInfo.InvariantCulture))
                    .Replace("{minutes}", (duration / 60_000 % 60).ToString("D2", CultureInfo.InvariantCulture)) : ""
                    }
                }).ToArray()
            }
        };
    }
    public static DesignContext Player(PlayerPresentation p)
    {
        var v = Capabilities(SyncWatchPlayer.Actions, p.Allows);
        foreach (var (key, value) in new Dictionary<string, string> { ["namespace"] = "player", ["title"] = p.Title, ["hasSharedPosition"] = Flag(p.Allows("dismissSharedPosition")), ["sharedPositionText"] = PlayerPresentation.Time(p.SharedPositionMs ?? 0), ["roomNotice"] = p.RoomNotice, ["hasRoomNotice"] = Flag(p.RoomNotice.Length > 0), ["state"] = char.ToLowerInvariant(p.State.ToString()[0]) + p.State.ToString()[1..], ["position"] = Number(p.DisplayedPosition), ["duration"] = Number(p.DisplayedDuration), ["positionText"] = PlayerPresentation.Time(p.DisplayedPosition), ["durationText"] = PlayerPresentation.Time(p.DisplayedDuration), ["volume"] = Number(p.IsValid ? p.Volume : 0), ["playIcon"] = p.Playing ? "player.pause" : "player.play", ["playLabel"] = p.Playing ? "pause" : "play", ["volumeIcon"] = p.Muted ? "player.muted" : "player.volume", ["volumeLabel"] = p.Muted ? "unmuteMovie" : "muteMovie" }) v[key] = value;
        return new() { Values = v };
    }
    public static DesignContext Settings(SettingsEditor editor, string section)
    {
        var fields = SyncWatchSettings.Fields.Where(f => f.Section == section).Select(f => new DesignContext { Values = new() { ["id"] = f.Id, ["label"] = f.Label, ["kind"] = f.Kind is "choice" or "boolean" ? f.Kind : "text", ["value"] = editor.Values[f.Id], ["editable"] = Flag(editor.EditableFields.Contains(f.Id)), ["isRange"] = "false" }, Collections = new() { ["options"] = f.Options.Select(o => new DesignContext { Values = new() { ["id"] = o, ["label"] = "option" + char.ToUpperInvariant(o[0]) + o[1..] } }).ToArray() } }).ToArray();
        return new() { Values = new() { ["namespace"] = "settings", ["section"] = section, ["dialogTitle"] = "openSettings", ["status"] = !editor.CanSave ? "settingsReadOnly" : editor.State == SettingsVisualState.SavedNotice ? "changesSaved" : editor.Message, ["saved"] = Flag(editor.State == SettingsVisualState.SavedNotice), ["can.save"] = Flag(editor.CanSave), ["can.close"] = "true", ["can.cancel"] = "true", ["interfaceSection"] = Flag(section == "interface"), ["unavailable"] = Flag(!SyncWatchSettings.Fields.Any(f => f.Section == section && editor.EditableFields.Contains(f.Id))) }, Collections = new() { ["sections"] = Sections(SyncWatchSettings.Sections, section), ["fields"] = fields } };
    }
    public static DesignContext PlayerSettings(PlayerSettingsEditor editor, string section, string language)
    {
        var fields = SyncWatchPlayerSettings.Fields.Where(f => f.Section == section).ToArray();
        var rows = fields.Select(f => f.Row).Distinct().Order().Select(row => new DesignContext
        {
            Values = new() { ["id"] = row.ToString(CultureInfo.InvariantCulture) },
            Collections = new()
            {
                ["fields"] = fields.Where(f => f.Row == row).Select(f =>
        {
            string raw = editor.Values[f.Id], safe = PlayerSettingsEditor.Valid(raw, f) ? raw : SyncWatchPlayerSettings.Defaults[f.Id];
            return new DesignContext { Values = new() { ["id"] = f.Id, ["label"] = f.Label, ["kind"] = f.Kind, ["value"] = raw, ["number"] = safe, ["minimum"] = Number(f.Minimum), ["maximum"] = Number(f.Maximum), ["step"] = Number(f.Step), ["unitText"] = f.Unit == "unitNone" ? "" : " " + SyncWatchCopy.Text(f.Unit, language), ["isRange"] = Flag(f.Kind == "range"), ["editable"] = Flag(editor.Allows(f.Id)) }, Collections = new() { ["options"] = f.Options.Select(o => new DesignContext { Values = new() { ["id"] = o.Id, ["fieldID"] = f.Id, ["label"] = o.Label, ["selected"] = Flag(o.Id == raw), ["swatchColor"] = o.ColorToken is null ? "" : "@" + o.ColorToken } }).ToArray() } };
        }).ToArray()
            }
        }).ToArray();
        var context = Preview(editor.Values);
        foreach (var (key, value) in new Dictionary<string, string> { ["namespace"] = "playerSettings", ["section"] = section, ["dialogTitle"] = "playerSettings", ["status"] = !editor.CanSave ? "settingsReadOnly" : editor.Message, ["saved"] = Flag(editor.State == PlayerSettingsVisualState.SavedNotice), ["can.save"] = Flag(editor.CanSave), ["can.resetSection"] = Flag(editor.CanReset(section)), ["can.close"] = "true", ["can.cancel"] = "true", ["showSubtitle"] = Flag(section == "subtitles") }) context.Values[key] = value;
        return context with { Collections = new() { ["sections"] = Sections(SyncWatchPlayerSettings.Sections, section), ["rows"] = rows } };
    }
    public static DesignContext Preview(IReadOnlyDictionary<string, string> values)
    {
        string Setting(string id) { var f = SyncWatchPlayerSettings.Fields.Single(f => f.Id == id); return values.TryGetValue(id, out var value) && PlayerSettingsEditor.Valid(value, f) ? value : SyncWatchPlayerSettings.Defaults[id]; }
        var v = new[] { "subtitleSize", "subtitlePadding", "subtitleOffset", "subtitleFont", "outlineWidth" }.ToDictionary(id => id, Setting);
        v["subtitleTop"] = Flag(Setting("subtitlePosition") == "top");
        foreach (var (binding, id) in new[] { ("subtitleFill", "subtitleColor"), ("outlineFill", "outlineColor") }) v[binding] = "@" + SyncWatchPlayerSettings.Fields.Single(f => f.Id == id).Options.Single(o => o.Id == Setting(id)).ColorToken;
        return new() { Values = v };
    }
    public static DesignContext Call(CallPresentation p, bool hasRemote, bool hasLocal)
    {
        static string Bool(bool value) => value ? "true" : "false";
        var values = new Dictionary<string, string>
        {
            ["showRemote"] = Bool(p.ShowsRemoteSurface && hasRemote),
            ["showLocal"] = Bool(p.ShowsLocalPreview && hasLocal),
            ["previewLabel"] = p.PreviewLabel(hasLocal),
            ["callStateLabel"] = "callState" + p.State,
            ["maximumExtent"] = SyncWatchCall.MaximumExtent.ToString(CultureInfo.InvariantCulture),
            ["volume"] = p.DisplayedVolume.ToString(CultureInfo.InvariantCulture),
            ["pinned"] = Bool(p.Pinned),
            ["fullscreen"] = Bool(p.Fullscreen),
            ["minimized"] = Bool(p.Minimized),
            ["microphoneEnabled"] = Bool(p.MicrophoneEnabled == true),
            ["cameraEnabled"] = Bool(p.CameraEnabled == true),
            ["microphoneOff"] = Bool(p.MicrophoneEnabled == false),
            ["cameraOff"] = Bool(p.CameraEnabled == false),
            ["nextMicrophone"] = p.MicrophoneEnabled == true ? "0" : "1",
            ["nextCamera"] = p.CameraEnabled == true ? "0" : "1",
            ["pinLabel"] = p.Pinned ? "unpinCall" : "pinCall",
            ["fullscreenLabel"] = p.Fullscreen ? "exitFullscreenCall" : "fullscreenCall",
            ["fullscreenIcon"] = p.Fullscreen ? "call.restore" : "call.fullscreen",
            ["volumeIcon"] = p.DisplayedVolume > 0 ? "call.volume" : "call.muted",
            ["microphoneLabel"] = p.MicrophoneEnabled == true ? "microphoneOn" : "microphoneOff",
            ["cameraLabel"] = p.CameraEnabled == true ? "cameraOn" : "cameraOff"
        };
        foreach (string action in SyncWatchCall.Actions) values["can." + action] = Bool(p.Allows(action));
        return new() { Values = values };
    }
}
