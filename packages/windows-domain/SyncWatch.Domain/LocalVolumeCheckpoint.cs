namespace SyncWatch.Domain;

// Remembers observed local volume after a successful user command, including delayed changes.
public sealed class LocalVolumeCheckpoint(PlaybackScope scope)
{
    private bool active;
    private ulong? high;
    private double? last;
    public void Submitted() => active = true;
    public double? Observe(PlaybackObservation value)
    {
        value.Validate();
        if (!active || value.Scope != scope || value.Status != PlaybackStatus.Ready || value.Volume is not double actual
            || high is ulong sequence && value.Sequence <= sequence) return null;
        high = value.Sequence;
        if (last == actual) return null;
        last = actual;
        return actual;
    }
}
