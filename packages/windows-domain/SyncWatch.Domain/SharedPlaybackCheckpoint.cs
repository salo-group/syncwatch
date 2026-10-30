namespace SyncWatch.Domain;

// Retains a scoped observation for an explicit user seek; never advances or acknowledges playback.
public sealed class SharedPlaybackCheckpoint(PlaybackScope scope)
{
    private PlaybackObservation? last, checkpoint;
    private ulong high;
    public void Observe(PlaybackObservation value, bool available)
    {
        value.Validate();
        if (value.Scope != scope || value.Sequence < high) return;
        high = value.Sequence;
        if (value.Ended || value.Status == PlaybackStatus.Failed || value.DurationMs is not null && checkpoint is not null && checkpoint.DurationMs != value.DurationMs) Clear();
        if (!available || checkpoint is not null) return;
        last = value.Status == PlaybackStatus.Ready && value.Seekable && !value.Buffering && !value.Ended && value.DurationMs > 0 ? value : null;
    }
    public void Interrupt()
    {
        checkpoint ??= last;
        last = null;
    }
    public long? Position(long durationMs) => checkpoint?.DurationMs == durationMs ? checkpoint.PositionMs : null;
    public void Clear() { last = null; checkpoint = null; }
}
