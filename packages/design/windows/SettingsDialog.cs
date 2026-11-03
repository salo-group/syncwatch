using System.Collections.ObjectModel;
using System.Windows.Controls;

namespace SyncWatch.Design;

public enum SettingsVisualState { Editing, SavedNotice, InvalidConfiguration }

public sealed class SettingsEditor
{
    private Dictionary<string, string> values;
    private Dictionary<string, string> original;
    public IReadOnlyDictionary<string, string> Values => new ReadOnlyDictionary<string, string>(new Dictionary<string, string>(values));
    public IReadOnlySet<string> EditableFields { get; }
    public bool CanSave { get; }
    public SettingsVisualState State { get; private set; }
    public string Message { get; private set; } = "";

    public SettingsEditor(IReadOnlyDictionary<string, string>? initial = null, IEnumerable<string>? editableFields = null,
        bool canSave = false, SettingsVisualState state = SettingsVisualState.Editing)
    {
        values = new(SyncWatchSettings.Defaults);
        foreach (var pair in initial ?? new Dictionary<string, string>()) values[pair.Key] = pair.Value;
        original = new(values); EditableFields = new HashSet<string>(editableFields ?? []); CanSave = canSave; State = state;
        if (state == SettingsVisualState.InvalidConfiguration) Message = "invalidConfiguration";
    }

    public void Edit(string id, string value)
    {
        if (!EditableFields.Contains(id) || !SyncWatchSettings.Fields.Any(f => f.Id == id && f.Kind != "folder")) return;
        values[id] = value; State = SettingsVisualState.Editing; Message = "";
    }

    public bool IsValid => values.Keys.ToHashSet().SetEquals(SyncWatchSettings.Defaults.Keys)
        && EditableFields.All(SyncWatchSettings.Defaults.ContainsKey) && SyncWatchSettings.Fields.All(descriptor =>
            values.TryGetValue(descriptor.Id, out var value) && value.Length <= descriptor.MaxLength && !value.Any(c => c < 32 || c is >= (char)127 and <= (char)159)
            && (descriptor.Kind != "choice" || descriptor.Options.Contains(value)) && (descriptor.Kind != "boolean" || value is "true" or "false"));

    public bool Save(Func<IReadOnlyDictionary<string, string>, bool> persist)
    {
        if (!CanSave) return false;
        if (!IsValid) { State = SettingsVisualState.InvalidConfiguration; Message = "invalidConfiguration"; return false; }
        if (!persist(Values)) { State = SettingsVisualState.InvalidConfiguration; Message = "saveFailed"; return false; }
        original = new(values); State = SettingsVisualState.SavedNotice; Message = "changesSaved"; return true;
    }

    public void Cancel() { values = new(original); State = SettingsVisualState.Editing; Message = ""; }
}

public sealed class SettingsDialog : UserControl
{
    public SettingsEditor Editor { get; }
    public string UiLanguage { get; private set; }
    public string Section { get; private set; }
    public Func<IReadOnlyDictionary<string, string>, bool> Persist { get; set; } = _ => false;
    private string folderPath = "";
    private bool canChooseFolder;
    public event Action? ChooseFolderRequested;
    public event Action? CloseRequested;
    public event Action? MoveRequested;
    public event Action<System.Windows.Vector>? MoveDeltaRequested;
    private System.Windows.Vector lastDrag;
    private readonly DesignSurface surface = new("Settings");
    public SettingsDialog(SettingsEditor? editor = null, string language = "en", string section = "interface")
    {
        Editor = editor ?? new(); UiLanguage = language; Section = SyncWatchSettings.Sections.Contains(section) ? section : "interface"; Content = surface; surface.ConfigureHost(this);
        surface.EventRequested += e =>
        {
            switch (e.Action)
            {
                case "selectSection": if (e.Value is string selected) SelectSection(selected); return;
                case "editField": if (e.Payload is string id && e.Value is string value) Editor.Edit(id, value); break;
                case "chooseFolder": if (canChooseFolder && Editor.CanSave) ChooseFolderRequested?.Invoke(); break;
                case "save": Editor.Save(values => Persist(values)); break;
                case "close": case "cancel": Editor.Cancel(); CloseRequested?.Invoke(); break;
                case "moveDialog":
                    if (e.Payload == "begin") { lastDrag = new(); MoveRequested?.Invoke(); }
                    else if (e.Value is string point) { var values = point.Split(',').Select(v => double.Parse(v, System.Globalization.CultureInfo.InvariantCulture)).ToArray(); if (values.Length == 2) { var current = new System.Windows.Vector(values[0], values[1]); MoveDeltaRequested?.Invoke(current - lastDrag); lastDrag = current; } }
                    else lastDrag = new();
                    break;
            }
            Refresh();
        }; Refresh();
    }
    public void UpdateFolder(string path, bool canChoose) { folderPath = path; canChooseFolder = canChoose; Refresh(); }
    public void SetLanguage(string language) { UiLanguage = language; Refresh(); }
    public void SelectSection(string section) { if (SyncWatchSettings.Sections.Contains(section)) { Section = section; Refresh(); } }
    private void Refresh() => surface.Render(DesignBindings.Settings(Editor, Section, folderPath, canChooseFolder), UiLanguage);
}
