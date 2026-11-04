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
            ResumePositions = [new() { MediaId = "retained-film", PositionMs = 12345 }]
        };
        try
        {
            disk.Write(initial);
            var preferences = new AppearancePreferences(disk);
            var draft = preferences.CreatePlayerSettingsEditor();
            Require(draft.EditableFields.SetEquals(["skipSeconds", "defaultVolume", "rememberVolume"]) && draft.Values["skipSeconds"] == "42" && !draft.IsValid);
            draft.Edit("skipSeconds", "30"); draft.Cancel();
            Require(preferences.State.Preferences.SkipSeconds == 42 && draft.Values["skipSeconds"] == "42");
            draft.Edit("skipSeconds", "30"); Require(draft.Save(preferences.SavePlayerSettings));
            Require(await preferences.PersistWatchStateAsync(initial));
            var restored = new AppearancePreferences(disk).State;
            Require(restored.Preferences == (initial.Preferences with { SkipSeconds = 30, DefaultMovieVolume = preferences.DefaultMovieVolume }));
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
            var volumeDisk = new FileLocalStateStore(Path.Combine(root, "volume.json")); volumeDisk.Write(initial);
            Require(new AppearancePreferences(new FileLocalStateStore(Path.Combine(root, "fresh-volume.json"))).OpeningMovieVolume == 0.5);
            var volume = new AppearancePreferences(volumeDisk);
            Require(volume.OpeningMovieVolume == 0.4);
            var volumeDraft = volume.CreatePlayerSettingsEditor(); volumeDraft.Edit("skipSeconds", "10");
            volumeDraft.Edit("defaultVolume", "25"); volumeDraft.Edit("rememberVolume", "false");
            Require(volumeDraft.Save(volume.SavePlayerSettings) && volume.OpeningMovieVolume == 0.25);
            Require(!volume.RememberMovieVolume(0.6) && new AppearancePreferences(volumeDisk).OpeningMovieVolume == 0.25);
            volumeDraft = volume.CreatePlayerSettingsEditor(); volumeDraft.Edit("rememberVolume", "true");
            Require(volumeDraft.Save(values => volume.SavePlayerSettings(values, 0.55)) && new AppearancePreferences(volumeDisk).OpeningMovieVolume == 0.55 && volume.RememberMovieVolume(0.6));
            Require(!volume.RememberMovieVolume(0.6) && !volume.RememberMovieVolume(double.NaN) && !volume.RememberMovieVolume(1.1));
            Require(await volume.PersistWatchStateAsync(initial));
            var volumeRestored = new AppearancePreferences(volumeDisk);
            Require(volumeRestored.OpeningMovieVolume == 0.6 && volumeRestored.DefaultMovieVolume == 0.25);
            Require(volumeRestored.State.SelectedMediaId == initial.SelectedMediaId && volumeRestored.State.ResumePositions.SequenceEqual(initial.ResumePositions));
            foreach (string value in new[] { "-5", "101", "2", "NaN" })
                Require(!volume.SavePlayerSettings(new Dictionary<string, string> { ["skipSeconds"] = "10", ["defaultVolume"] = value }));
            try { (initial.Preferences with { DefaultMovieVolume = double.PositiveInfinity }).Validate(); throw new InvalidOperationException("invalid volume accepted"); }
            catch (WatchException) { }
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
