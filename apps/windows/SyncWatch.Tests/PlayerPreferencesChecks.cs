using System.IO;
using SyncWatch;
using SyncWatch.Domain;

internal static class PlayerPreferencesChecks
{
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("playback-preferences"); }
    internal static async Task RunAsync()
    {
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-player-preferences-" + Guid.NewGuid());
        string path = Path.Combine(root, "local-state.json");
        var disk = new FileLocalStateStore(path);
        var initial = LocalState.CreateDefault() with
        {
            Preferences = LocalPreferences.CreateDefault() with { Language = "ru", MovieVolume = 0.4, SkipSeconds = 42 },
            SelectedMediaId = "retained-film",
            ResumePositions = [new("retained-film", 12345)]
        };
        try
        {
            disk.Write(initial);
            var preferences = new AppearancePreferences(disk);
            var draft = preferences.CreatePlayerSettingsEditor();
            Require(draft.EditableFields.SetEquals(["skipSeconds"]) && draft.Values["skipSeconds"] == "42" && !draft.IsValid);
            draft.Edit("skipSeconds", "30"); draft.Cancel();
            Require(preferences.State.Preferences.SkipSeconds == 42 && draft.Values["skipSeconds"] == "42");
            draft.Edit("skipSeconds", "30"); Require(draft.Save(preferences.SavePlayerSettings));
            Require(await preferences.PersistWatchStateAsync(initial));
            var restored = new AppearancePreferences(disk).State;
            Require(restored.Preferences == (initial.Preferences with { SkipSeconds = 30 }));
            Require(restored.SelectedMediaId == initial.SelectedMediaId && restored.ResumePositions.SequenceEqual(initial.ResumePositions));
            foreach (string value in new[] { "", "0", "42", "600", "30.0", "+30", " 30", "30\n" })
                Require(!preferences.SavePlayerSettings(new Dictionary<string, string> { ["skipSeconds"] = value }));
            const string future = "{\"version\":2,\"future\":true}";
            File.WriteAllText(path, future);
            Require(!preferences.SavePlayerSettings(new Dictionary<string, string> { ["skipSeconds"] = "15" }));
            Require(preferences.State.Preferences.SkipSeconds == 30 && File.ReadAllText(path) == future);
            var readOnly = new AppearancePreferences(disk);
            Require(!readOnly.CreatePlayerSettingsEditor().CanSave && !readOnly.SavePlayerSettings(new Dictionary<string, string> { ["skipSeconds"] = "15" }));
            var failed = new AppearancePreferences(new FailingStore());
            var failedDraft = failed.CreatePlayerSettingsEditor(); failedDraft.Edit("skipSeconds", "60");
            Require(!failedDraft.Save(failed.SavePlayerSettings) && failedDraft.Message == "saveFailed" && failed.State.Preferences.SkipSeconds == 10);
            using var gate = new GatedStore();
            var concurrent = new AppearancePreferences(gate);
            var writing = concurrent.PersistWatchStateAsync(initial);
            try
            {
                await gate.Entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
                Require(concurrent.SavingWatchState && !concurrent.CreatePlayerSettingsEditor().CanSave);
                Require(!concurrent.SavePlayerSettings(new Dictionary<string, string> { ["skipSeconds"] = "30" }));
            }
            finally { gate.Proceed.Set(); await writing; }
            Console.WriteLine("SYNCWATCH_PLAYER_PREFERENCES_OK transactional write, checkpoint serialization, restart, future version and failure");
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }
    private sealed class GatedStore : ILocalStateStore, IDisposable
    {
        internal readonly TaskCompletionSource Entered = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly ManualResetEventSlim Proceed = new();
        public LocalStateRead Read() => new(LocalStateStatus.Missing);
        public void Write(LocalState state)
        {
            Entered.TrySetResult();
            if (!Proceed.Wait(TimeSpan.FromSeconds(15))) throw new IOException("owned writer timeout");
        }
        public void Dispose() => Proceed.Dispose();
    }
    private sealed class FailingStore : ILocalStateStore
    {
        public LocalStateRead Read() => new(LocalStateStatus.Missing, null);
        public void Write(LocalState state) => throw new IOException("owned write failure");
    }
}
