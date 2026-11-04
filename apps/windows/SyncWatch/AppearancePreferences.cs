using System.IO;
using SyncWatch.Design;
using SyncWatch.Domain;

namespace SyncWatch;

public sealed class AppearancePreferences
{
    private readonly SerializedLocalStateStore store;
    public bool SavingWatchState { get; private set; }
    public LocalState State { get; private set; } = LocalState.CreateDefault();
    public bool CanWrite { get; }
    public static string DefaultPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SyncWatch", "local-state.json");

    public AppearancePreferences(ILocalStateStore store)
    {
        this.store = new SerializedLocalStateStore(store);
        try
        {
            var loaded = this.store.Read();
            if (loaded.Status == LocalStateStatus.Loaded && loaded.State is not null)
            {
                loaded.State.Validate(); State = loaded.State; CanWrite = true;
            }
            else
            {
                CanWrite = loaded.Status is LocalStateStatus.Missing or LocalStateStatus.Invalid;
                if (CanWrite) State = State with { Preferences = State.Preferences with { MovieVolume = DefaultMovieVolume } };
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or WatchException or ContractException) { CanWrite = false; }
    }

    public SettingsEditor CreateEditor() => new(new Dictionary<string, string> { ["theme"] = State.Preferences.Theme, ["language"] = State.Preferences.Language }, CanWrite && !SavingWatchState ? ["theme", "language"] : [], CanWrite && !SavingWatchState);

    public bool Save(IReadOnlyDictionary<string, string> values, bool libraryOptions = false)
    {
        if (!CanWrite || SavingWatchState || !values.TryGetValue("theme", out var theme) || !values.TryGetValue("language", out var language)) return false;
        var next = State with { Preferences = State.Preferences with { Theme = theme, Language = language } };
        if (libraryOptions)
        {
            if (!values.TryGetValue("scanSubfolders", out var scan) || scan is not ("true" or "false") ||
                !values.TryGetValue("automaticRefresh", out var refresh) || refresh is not ("true" or "false")) return false;
            next = next with { Preferences = next.Preferences with { ScanSubfolders = scan == "true", AutomaticRefresh = refresh == "true" } };
        }
        try { next.Validate(); store.Write(next); State = next; return true; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or WatchException or ContractException) { return false; }
    }
    public double DefaultMovieVolume => State.Preferences.DefaultMovieVolume ?? double.Parse(SyncWatchPlayerSettings.Defaults["defaultVolume"], System.Globalization.CultureInfo.InvariantCulture) / 100;
    public double OpeningMovieVolume => State.Preferences.RememberMovieVolume ? State.Preferences.MovieVolume : DefaultMovieVolume;
    // The watch checkpoint coalesces this acknowledged local change with current playback history.
    public bool RememberMovieVolume(double value)
    {
        if (!CanWrite || !State.Preferences.RememberMovieVolume || !double.IsFinite(value) || value is < 0 or > 1 || value == State.Preferences.MovieVolume) return false;
        State = State with { Preferences = State.Preferences with { MovieVolume = value } }; return true;
    }
    public PlayerSettingsEditor CreatePlayerSettingsEditor() => new(
        new Dictionary<string, string>
        {
            ["skipSeconds"] = State.Preferences.SkipSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["defaultVolume"] = PlayerSettingsEditor.Canonical(DefaultMovieVolume * 100),
            ["rememberVolume"] = State.Preferences.RememberMovieVolume ? "true" : "false"
        },
        CanWrite && !SavingWatchState ? ["skipSeconds", "defaultVolume", "rememberVolume"] : [], CanWrite && !SavingWatchState);

    public bool SavePlayerSettings(IReadOnlyDictionary<string, string> values) => SavePlayerSettings(values, null);
    public bool SavePlayerSettings(IReadOnlyDictionary<string, string> values, double? currentMovieVolume)
    {
        if (!CanWrite || SavingWatchState || !values.TryGetValue("skipSeconds", out var value)) return false;
        var field = SyncWatchPlayerSettings.Fields.Single(f => f.Id == "skipSeconds");
        if (!PlayerSettingsEditor.Valid(value, field) || !int.TryParse(value, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out int seconds)) return false;
        var updated = State.Preferences with { SkipSeconds = seconds };
        if (values.TryGetValue("defaultVolume", out var volume))
        {
            if (!PlayerSettingsEditor.Valid(volume, SyncWatchPlayerSettings.Fields.Single(f => f.Id == "defaultVolume"))) return false;
            updated = updated with { DefaultMovieVolume = double.Parse(volume, System.Globalization.CultureInfo.InvariantCulture) / 100 };
        }
        if (values.TryGetValue("rememberVolume", out var remember))
        {
            if (remember is not ("true" or "false")) return false;
            if (remember == "true" && !State.Preferences.RememberMovieVolume && currentMovieVolume is double current)
            {
                if (!double.IsFinite(current) || current is < 0 or > 1) return false;
                updated = updated with { MovieVolume = current };
            }
            updated = updated with { RememberMovieVolume = remember == "true" };
        }
        var next = State with { Preferences = updated };
        try { next.Validate(); store.Write(next); State = next; return true; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or WatchException or ContractException) { return false; }
    }

    public async Task<bool> PersistWatchStateAsync(LocalState value)
    {
        if (SavingWatchState) return false;
        var next = value with { Preferences = State.Preferences };
        try { next.Validate(); }
        catch (Exception error) when (error is WatchException or ContractException) { return false; }
        State = next;
        if (!CanWrite) return false;
        SavingWatchState = true;
        try { await Task.Run(() => store.Write(next)); return true; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or WatchException or ContractException) { return false; }
        finally { SavingWatchState = false; }
    }
}
