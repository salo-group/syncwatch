using System.IO;
using SyncWatch;
using SyncWatch.Domain;
using SyncWatch.Library;

HelperRuntimeChecks.Run();
await RoomDispatcherChecks.RunAsync(async () =>
{
    await PlayerPreferencesChecks.RunAsync();
    WatchPlaylistChecks.Run();
    LibraryTrackSelectionChecks.Run();
    await LibraryRefreshChecks.RunAsync();
    await LibraryMetadataChecks.RunAsync();
    await LocalPlaybackTracksChecks.RunAsync();
    await RoomPlaybackGateChecks.RunAsync();
    await RoomPublicationChecks.RunAsync();
    await RoomPublicationLiveChecks.RunAsync();
    await RoomWatchContextChecks.RunAsync();
});

string root = Path.Combine(Path.GetTempPath(), "syncwatch-media-preparation-" + Guid.NewGuid());
Directory.CreateDirectory(root);
try
{
    File.WriteAllText(Path.Combine(root, "owned.avi"), "authored fixture");
    var access = LibraryFolderAccess.Selected(root);
    var media = (await new FileSystemLibrary().ScanAsync(root, access.RootId, false)).Single();
    var selection = new LibraryMediaSelection(media, access);
    foreach (bool closeOwner in new[] { false, true })
    {
        var preparation = new SharedMediaPreparation(); var player = new WaitingMediaPlayer();
        using var caller = new CancellationTokenSource();
        var worker = preparation.PrepareAsync(selection, player, PlaybackScope.Create(), caller.Token);
        try
        {
            await player.Entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
            if (closeOwner) await preparation.CloseAsync().WaitAsync(TimeSpan.FromSeconds(2)); else caller.Cancel();
            await player.Stopped.Task.WaitAsync(TimeSpan.FromSeconds(1));
            try { await worker; throw new InvalidOperationException("Canceled preparation succeeded"); } catch (OperationCanceledException) { }
            await preparation.CloseAsync(); await preparation.CloseAsync();
        }
        finally { await preparation.CloseAsync(); try { await worker; } catch (OperationCanceledException) { } }
    }
    Console.WriteLine("SYNCWATCH_SHARED_MEDIA_PREPARATION_OK caller cancellation and repeated owner close");
}
finally { Directory.Delete(root, true); }

internal sealed class WaitingMediaPlayer : IPlaybackPort
{
    internal readonly TaskCompletionSource Entered = new(TaskCreationOptions.RunContinuationsAsynchronously);
    internal readonly TaskCompletionSource Stopped = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public async Task<PlaybackObservation> ObserveAsync(PlaybackScope scope, CancellationToken cancellation = default)
    {
        Entered.TrySetResult();
        try { await Task.Delay(TimeSpan.FromSeconds(30), cancellation); throw new InvalidOperationException("unexpected observation"); }
        finally { Stopped.TrySetResult(); }
    }
    public Task LoadAsync(string localFile, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task SetPausedAsync(bool paused, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task SeekAsync(long positionMs, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task SetRateAsync(double rate, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task SetVolumeAsync(double volume, bool muted, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task<IReadOnlyList<PlaybackTrack>> TracksAsync(bool subtitles, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task SelectTrackAsync(int id, bool subtitles, PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public Task StopAsync(PlaybackScope scope, CancellationToken cancellation = default) => throw new NotSupportedException();
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}
