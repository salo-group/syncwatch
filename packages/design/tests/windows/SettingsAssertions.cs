using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SyncWatch;
using SyncWatch.Design;
using SyncWatch.Domain;

internal static class SettingsAssertions
{
    private static void Check(bool condition, string name) { if (!condition) throw new InvalidOperationException("Settings assertion: " + name); }
    private static IEnumerable<T> Find<T>(DependencyObject root) where T : DependencyObject
    {
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T value) yield return value;
            foreach (var descendant in Find<T>(child)) yield return descendant;
        }
    }
    private static T Element<T>(DependencyObject root, string id) where T : FrameworkElement => Find<T>(root).Single(e => AutomationProperties.GetAutomationId(e) == id);
    private static void Layout(FrameworkElement element)
    {
        element.Measure(new Size(680, 420)); element.Arrange(new Rect(0, 0, 680, 420)); element.UpdateLayout();
    }
    private sealed class Store : ILocalStateStore
    {
        public LocalStateRead Current { get; set; } = new(LocalStateStatus.Missing);
        public bool Fail { get; set; }
        public int Writes { get; private set; }
        public LocalStateRead Read() => Current;
        public void Write(LocalState state) { if (Fail) throw new IOException("fixture"); Writes++; Current = new(LocalStateStatus.Loaded, state); }
    }
    public static void Run(Application application, ResourceDictionary dictionary)
    {
        var model = new SettingsEditor(editableFields: ["theme", "language"], canSave: true);
        model.Edit("folder", "ignored"); model.Edit("unknown", "ignored"); Check(model.Values["folder"] == "" && !model.Values.ContainsKey("unknown"), "capabilities");
        int writes = 0; model.Edit("theme", "invalid"); Check(!model.Save(_ => { writes++; return true; }) && writes == 0, "invalid data cannot reach persistence");
        model.Cancel(); Check(model.Values["theme"] == "system", "cancel discards invalid draft"); model.Edit("theme", "dark");
        Check(!model.Save(_ => false) && model.State == SettingsVisualState.InvalidConfiguration && model.Message == "saveFailed", "failed write has no saved notice");
        Check(model.Save(_ => { writes++; return true; }) && model.State == SettingsVisualState.SavedNotice, "save acknowledged");
        var snapshot = model.Values; model.Edit("theme", "light"); Check(snapshot["theme"] == "dark", "save receives a snapshot"); model.Cancel(); Check(model.Values["theme"] == "dark", "cancel preserves last saved base");
        Check(!new SettingsEditor().Save(_ => { writes++; return true; }) && writes == 1, "readonly cannot save");
        Check(!new SettingsEditor(new Dictionary<string, string> { ["unknown"] = "value" }).IsValid, "unknown initial field");
        var controlCharacters = new SettingsEditor(editableFields: ["username"], canSave: true); controlCharacters.Edit("username", "bad\u007f"); Check(!controlCharacters.IsValid, "control characters are invalid");
        var store = new Store { Current = new(LocalStateStatus.Loaded, LocalState.CreateDefault() with { Preferences = LocalPreferences.CreateDefault() with { SkipSeconds = 42 }, SelectedMediaId = "movie-1", ResumePositions = [new ResumePosition { MediaId = "movie-1", PositionMs = 9000 }] }) };
        var preferences = new AppearancePreferences(store);
        Check(preferences.CreateEditor().EditableFields.SetEquals(new[] { "theme", "language" }), "appearance capabilities");
        Check(preferences.Save(new Dictionary<string, string> { ["theme"] = "dark", ["language"] = "ru", ["folder"] = "not-persisted" }), "appearance save");
        Check(preferences.State.Preferences.SkipSeconds == 42 && preferences.State.SelectedMediaId == "movie-1" && preferences.State.ResumePositions[0].PositionMs == 9000, "other local state is retained");
        store.Fail = true; Check(!preferences.Save(new Dictionary<string, string> { ["theme"] = "light", ["language"] = "en" }) && preferences.State.Preferences.Theme == "dark" && store.Writes == 1, "failed persistence cannot publish appearance");
        store.Current = new(LocalStateStatus.UnsupportedVersion); var future = new AppearancePreferences(store); Check(!future.CanWrite && !future.CreateEditor().CanSave, "future state stays readonly");
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-appearance-" + Guid.NewGuid().ToString("N"));
        try
        {
            var disk = new FileLocalStateStore(Path.Combine(directory, "local-state.json"));
            Check(new AppearancePreferences(disk).Save(new Dictionary<string, string> { ["theme"] = "light", ["language"] = "ru" }), "real store save");
            var restored = new AppearancePreferences(disk); Check(restored.State.Preferences.Theme == "light" && restored.State.Preferences.Language == "ru", "restore persisted appearance");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
        var editor = new SettingsEditor(editableFields: ["theme", "language"], canSave: true);
        var view = new SettingsDialog(editor); Layout(view);
        Element<ComboBox>(view, "settings.field.theme").SelectedValue = "dark"; Check(editor.Values["theme"] == "dark", "native choice edits draft");
        view.Persist = _ => false; Element<Button>(view, "settings.save").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Check(editor.State == SettingsVisualState.InvalidConfiguration, "native save failure");
        var nativeStore = new Store(); var nativePreferences = new AppearancePreferences(nativeStore);
        Element<ComboBox>(view, "settings.field.language").SelectedValue = "ru";
        view.Persist = values => { if (!nativePreferences.Save(values)) return false; view.SetLanguage(nativePreferences.State.Preferences.Language); return true; };
        Element<Button>(view, "settings.save").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(editor.State == SettingsVisualState.SavedNotice && nativeStore.Writes == 1 && view.UiLanguage == "ru" && nativePreferences.State.Preferences.Theme == "dark", "native save applies appearance and rerenders language");
        Element<Button>(view, "settings.section.library").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Layout(view);
        Check(view.Section == "library" && editor.Values["theme"] == "dark" && !Element<TextBox>(view, "settings.field.folder").IsEnabled, "tabs retain draft and unavailable fields are disabled");
        int choices = 0; view.ChooseFolderRequested += () => choices++;
        string folderPath = "C:/Movies/Кино " + string.Concat(Enumerable.Repeat("long-path/", 70));
        view.UpdateFolder(folderPath, true); Layout(view);
        Check(Element<TextBox>(view, "settings.field.folder").Text == folderPath && editor.IsValid, "observed long path is independent of draft validation");
        Element<Button>(view, "settings.chooseFolder").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(choices == 1 && editor.Values["theme"] == "dark" && editor.Values["folder"] == "", "chooser leaves draft untouched");
        view.UpdateFolder(folderPath, false); Layout(view);
        Element<Button>(view, "settings.chooseFolder").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(choices == 1 && !Element<Button>(view, "settings.chooseFolder").IsEnabled, "scan blocks forged chooser event");
        var readonlyView = new SettingsDialog(section: "library"); readonlyView.UpdateFolder(folderPath, true); Layout(readonlyView);
        readonlyView.ChooseFolderRequested += () => choices++;
        Element<Button>(readonlyView, "settings.chooseFolder").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(choices == 1 && !Element<Button>(readonlyView, "settings.chooseFolder").IsEnabled, "readonly blocks chooser");
        Check(KeyboardNavigation.GetTabNavigation(view) == KeyboardNavigationMode.Cycle, "keyboard remains in dialog");
        Check(Element<Button>(view, "settings.save").IsDefault && Element<Button>(view, "settings.cancel").IsCancel && Element<Button>(view, "settings.cancel").TabIndex < Element<Button>(view, "settings.save").TabIndex, "default/cancel keys and footer tab order");
        bool closed = false; view.CloseRequested += () => closed = true; editor.Edit("theme", "light"); Element<Button>(view, "settings.cancel").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Check(closed && editor.Values["theme"] == "dark", "native cancel");
        string? output = Environment.GetEnvironmentVariable("SYNCWATCH_DESIGN_SCREENSHOTS");
        if (output is not null) Directory.CreateDirectory(output);
        foreach (var theme in new[] { "dark", "light" })
        {
            application.Resources.MergedDictionaries.Add((ResourceDictionary)dictionary["theme." + theme]);
            foreach (var section in SyncWatchSettings.Sections)
                foreach (var state in Enum.GetValues<SettingsVisualState>())
                    foreach (var locale in new[] { "en", "ru" })
                    {
                        var fixture = new SettingsEditor(editableFields: SyncWatchSettings.Defaults.Keys, canSave: true, state: state);
                        var dialog = new SettingsDialog(fixture, locale, section); Layout(dialog);
                        foreach (var choice in Find<ComboBox>(dialog))
                            if (choice.SelectedItem is KeyValuePair<string, string> selected)
                                Check(Element<TextBlock>(choice, "settings.choiceLabel").Text == selected.Value, "choice displays localized label without internal ID");
                        var image = new RenderTargetBitmap(680, 420, 96, 96, PixelFormats.Pbgra32); image.Render(dialog);
                        var pixels = new byte[680 * 420 * 4]; image.CopyPixels(pixels, 680 * 4, 0);
                        int offset = (340 * 680 + 650) * 4; var expected = ((SolidColorBrush)application.Resources["theme.surface.brush"]).Color;
                        Check(pixels[offset + 3] == 255 && Math.Abs(pixels[offset] - expected.B) <= 1 && Math.Abs(pixels[offset + 1] - expected.G) <= 1 && Math.Abs(pixels[offset + 2] - expected.R) <= 1, "native settings background matches theme");
                        if (output is not null)
                        {
                            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image));
                            using var file = File.Create(Path.Combine(output, $"settings{section}{state}-{theme}-{locale}-680x420.png")); encoder.Save(file);
                        }
                    }
            application.Resources.MergedDictionaries.Remove((ResourceDictionary)dictionary["theme." + theme]);
        }
        Console.WriteLine("Verified settings draft/save/cancel/capabilities, native choices/tab order, real appearance persistence and 48 native settings renders");
    }
}
