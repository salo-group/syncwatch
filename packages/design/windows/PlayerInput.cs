namespace SyncWatch.Design;

public static class PlayerInput
{
    public static bool DispatchKey(this PlayerPresentation presentation, string key, Action<string, double?> handler,
        bool repeating = false, bool fullscreen = false, bool modified = false, bool protectedFocus = false)
    {
        if (modified || protectedFocus) return false;
        var binding = SyncWatchPlayer.InputBindings.FirstOrDefault(b => b.Key == key);
        if (binding is null || binding.FullscreenOnly && !fullscreen) return false;
        if (repeating && !binding.Repeats) return true;
        if (binding.Action == "setMovieVolume") VolumeStep(presentation, binding.Value ?? 0, handler);
        else presentation.Dispatch(binding.Action, binding.Value, handler);
        return true;
    }
    public static bool DispatchWheel(this PlayerPresentation presentation, double direction, Action<string, double?> handler,
        bool modified = false, bool protectedFocus = false)
    {
        if (modified || protectedFocus || !double.IsFinite(direction) || direction == 0) return false;
        VolumeStep(presentation, Math.Sign(direction), handler);
        return true;
    }
    private static void VolumeStep(PlayerPresentation presentation, double direction, Action<string, double?> handler)
    {
        if (!presentation.IsValid) return;
        double value = Math.Clamp(presentation.Volume + direction * SyncWatchPlayer.InputVolumeStep, 0, 1);
        if (value != presentation.Volume) presentation.Dispatch("setMovieVolume", value, handler);
    }
}
