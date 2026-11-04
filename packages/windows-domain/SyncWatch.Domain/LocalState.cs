using System.Collections.Immutable;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace SyncWatch.Domain;

/// <summary>Initial local settings boundary; engine-specific settings arrive with their adapters.</summary>
public sealed record LocalPreferences
{
    public required string Theme { get; init; }
    public required string Language { get; init; }
    public required int SkipSeconds { get; init; }
    public required bool PreviewsEnabled { get; init; }
    public required int PreviewCacheMB { get; init; }
    public required int CursorHideDelayMs { get; init; }
    public double? DefaultMovieVolume { get; init; }
    public required double MovieVolume { get; init; }
    public required double CallVolume { get; init; }
    public required bool RememberMovieVolume { get; init; }
    public required bool DuckMovieDuringSpeech { get; init; }
    public required bool CameraEnabled { get; init; }
    public required bool MicrophoneEnabled { get; init; }
    // Older state files retain the selected folder's legacy values until Settings saves these options.
    public bool? ScanSubfolders { get; init; }
    public bool? AutomaticRefresh { get; init; }

    public static LocalPreferences CreateDefault() => new()
    {
        Theme = "system",
        Language = "en",
        SkipSeconds = 10,
        PreviewsEnabled = true,
        PreviewCacheMB = 150,
        CursorHideDelayMs = 2500,
        MovieVolume = 1,
        CallVolume = 1,
        RememberMovieVolume = true,
        DuckMovieDuringSpeech = false,
        CameraEnabled = true,
        MicrophoneEnabled = true
    };

    public void Validate()
    {
        if (Theme is not ("system" or "dark" or "light") || Language is not ("en" or "ru")
            || SkipSeconds is < 1 or > 600 || PreviewCacheMB is < 50 or > 500
            || CursorHideDelayMs is < 500 or > 10_000
            || (DefaultMovieVolume is double initial && (!double.IsFinite(initial) || initial is < 0 or > 1))
            || !double.IsFinite(MovieVolume) || MovieVolume is < 0 or > 1
            || !double.IsFinite(CallVolume) || CallVolume is < 0 or > 1)
        {
            throw new WatchException(WatchRejection.InvalidLocalState);
        }
    }
}

public sealed record ResumePosition
{
    public required string MediaId { get; init; }
    public required long PositionMs { get; init; }
}

/// <summary>No process identity, active session, call state, file path, or credential is persisted.</summary>
public sealed record LocalState
{
    public required int Version { get; init; }
    public required LocalPreferences Preferences { get; init; }
    public string? SelectedMediaId { get; init; }
    public required ImmutableArray<ResumePosition> ResumePositions { get; init; }

    public static LocalState CreateDefault() => new()
    {
        Version = 1,
        Preferences = LocalPreferences.CreateDefault(),
        ResumePositions = []
    };

    public void Validate()
    {
        if (Version != 1) { throw new WatchException(WatchRejection.UnsupportedVersion); }
        if (Preferences is null || ResumePositions.IsDefault || ResumePositions.Length > 256)
        {
            throw new WatchException(WatchRejection.InvalidLocalState);
        }
        Preferences.Validate();
        if (SelectedMediaId is not null) { WatchSession.ValidateId(SelectedMediaId); }
        HashSet<string> seen = [];
        foreach (ResumePosition resume in ResumePositions)
        {
            if (resume is null || !seen.Add(resume.MediaId))
            {
                throw new WatchException(WatchRejection.InvalidLocalState);
            }
            WatchSession.ValidateId(resume.MediaId);
            if (resume.PositionMs is < 0 or > WatchSession.MaximumPositionMs)
            {
                throw new WatchException(WatchRejection.InvalidLocalState);
            }
        }
    }

    public long ResumePositionFor(string mediaId) => ResumePositions.FirstOrDefault(resume => resume.MediaId == mediaId)?.PositionMs ?? 0;
}

public enum LocalStateStatus { Missing, Loaded, Invalid, UnsupportedVersion }
public sealed record LocalStateRead(LocalStateStatus Status, LocalState? State = null);

public interface ILocalStateStore
{
    LocalStateRead Read();
    void Write(LocalState state);
}

/// <summary>The caller chooses an application-owned file and serializes access.</summary>
public sealed class FileLocalStateStore(string path) : ILocalStateStore
{
    public const int MaximumBytes = 65_536;
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        MaxDepth = 32
    };
    private readonly string filePath = Path.GetFullPath(path);

    public LocalStateRead Read()
    {
        byte[] buffer = new byte[MaximumBytes + 1];
        int length;
        try
        {
            using FileStream stream = new(filePath, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            length = stream.ReadAtLeast(buffer, buffer.Length, throwOnEndOfStream: false);
        }
        catch (FileNotFoundException) { return new(LocalStateStatus.Missing); }
        catch (DirectoryNotFoundException) { return new(LocalStateStatus.Missing); }
        if (length > MaximumBytes) { return new(LocalStateStatus.Invalid); }
        try
        {
            using JsonDocument document = JsonDocument.Parse(buffer.AsMemory(0, length));
            if (document.RootElement.ValueKind != JsonValueKind.Object
                || !document.RootElement.TryGetProperty("version", out JsonElement version)
                || !version.TryGetInt32(out int number) || number < 0) { return new(LocalStateStatus.Invalid); }
            if (number != 1) { return new(LocalStateStatus.UnsupportedVersion); }
            LocalState? state = JsonSerializer.Deserialize<LocalState>(buffer.AsSpan(0, length), Options);
            if (state is null) { return new(LocalStateStatus.Invalid); }
            state.Validate();
            return new(LocalStateStatus.Loaded, state);
        }
        catch (Exception error) when (error is JsonException or WatchException or ContractException or InvalidOperationException)
        {
            return new(LocalStateStatus.Invalid);
        }
    }

    public void Write(LocalState state)
    {
        state.Validate();
        if (Read().Status == LocalStateStatus.UnsupportedVersion)
        {
            throw new WatchException(WatchRejection.UnsupportedVersion);
        }
        byte[] data = JsonSerializer.SerializeToUtf8Bytes(state, Options);
        if (data.Length > MaximumBytes) { throw new WatchException(WatchRejection.InvalidLocalState); }
        string directory = Path.GetDirectoryName(filePath)!;
        Directory.CreateDirectory(directory);
        string temporary = Path.Combine(directory, $".local-state-{Guid.NewGuid():N}.tmp");
        try
        {
            using (FileStream stream = new(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                stream.Write(data);
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, filePath, overwrite: true);
        }
        finally { File.Delete(temporary); }
    }
}
