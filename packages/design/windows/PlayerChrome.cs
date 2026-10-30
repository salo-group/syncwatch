using System.Globalization;
using System.Windows;
using System.Windows.Controls;

namespace SyncWatch.Design;

public enum PlayerVisualState { Loading, Paused, Playing, Buffering, Fullscreen, ConnectionInterrupted, Ended }

// Immutable observations/capabilities. Dispatching a request never changes the observed position/state.
public sealed record PlayerPresentation
{
    public PlayerVisualState State { get; init; }
    public string Title { get; init; } = "";
    public string RoomNotice { get; init; } = "";
    public long PositionMs { get; init; }
    public long DurationMs { get; init; }
    public double Volume { get; init; } = 1;
    public bool Muted { get; init; }
    public int SkipSeconds { get; init; } = 10;
    public bool HasPrevious { get; init; }
    public bool HasNext { get; init; }
    public IReadOnlySet<string> EnabledActions { get; init; } = new HashSet<string>();
    public bool IsValid => DurationMs is >= 0 and <= SyncWatchPlayer.MaximumPositionMs && PositionMs >= 0 && PositionMs <= DurationMs && double.IsFinite(Volume) && Volume is >= 0 and <= 1 && SkipSeconds is >= 1 and <= 600;
    public bool Playing => State is PlayerVisualState.Playing or PlayerVisualState.Fullscreen;
    public double DisplayedDuration => Math.Clamp(DurationMs, 0, SyncWatchPlayer.MaximumPositionMs);
    public double DisplayedPosition => Math.Clamp(PositionMs, 0, DisplayedDuration);
    public bool Allows(string action)
    {
        if (!EnabledActions.Contains(action) || !SyncWatchPlayer.Actions.Contains(action)) return false;
        return action switch
        {
            "playPause" or "seek" or "skip" or "previousFile" or "nextFile" or "selectAudio" or "selectSubtitles" => IsValid && (State is PlayerVisualState.Paused or PlayerVisualState.Playing or PlayerVisualState.Fullscreen || State == PlayerVisualState.Ended && SyncWatchPlayer.CompletedNavigationActions.Contains(action)) && (action switch
            {
                "seek" or "skip" => DurationMs > 0,
                "previousFile" => HasPrevious,
                "nextFile" => HasNext,
                _ => true,
            }),
            "setMovieVolume" or "muteMovie" => IsValid,
            _ => true,
        };
    }
    public void Dispatch(string action, double? value, Action<string, double?> handler)
    {
        if (!Allows(action)) return;
        switch (action)
        {
            case "seek": if (value is not double seek || !double.IsFinite(seek) || Math.Round(seek) != seek || seek < 0 || seek > DurationMs) return; break;
            case "skip":
                if (value is not (-1 or 1) || !Allows("seek")) return;
                double target = Math.Clamp(PositionMs + value.Value * SkipSeconds * 1000, 0, DurationMs);
                if (target != PositionMs) handler("seek", target);
                return;
            case "setMovieVolume": if (value is not double volume || !double.IsFinite(volume) || volume is < 0 or > 1) return; break;
            default: if (value is not null) return; break;
        }
        handler(action, value);
    }
    public static string Time(double milliseconds)
    {
        long seconds = (long)Math.Clamp(double.IsFinite(milliseconds) ? milliseconds : 0, 0, SyncWatchPlayer.MaximumPositionMs) / 1000;
        return seconds >= 3600 ? string.Create(CultureInfo.InvariantCulture, $"{seconds / 3600}:{seconds / 60 % 60:00}:{seconds % 60:00}") : string.Create(CultureInfo.InvariantCulture, $"{seconds / 60:00}:{seconds % 60:00}");
    }
    public static PlayerPresentation Gallery(PlayerVisualState state) => new()
    {
        State = state,
        Title = SyncWatchCopy.GalleryMovies[1],
        DurationMs = state == PlayerVisualState.Loading ? 0 : 5_400_000,
        PositionMs = state == PlayerVisualState.Loading ? 0 : state == PlayerVisualState.Ended ? 5_400_000 : 754_000,
        Volume = 0.7,
        HasPrevious = true,
        HasNext = true,
        EnabledActions = new HashSet<string>(SyncWatchPlayer.Actions),
    };
}

public sealed class PlayerChrome : UserControl
{
    public PlayerPresentation Presentation { get; private set; } = new();
    public string UiLanguage { get; private set; } = "en";
    public event Action<string, double?>? ActionRequested;
    public event Action<SelectionMenuRequest>? SelectionRequested;
    public event Action? SelectionDismissed;
    public SelectionMenuPresentation? SelectionPresentation { get; set; }
    public UIElement? VideoSurface { get; set; }
    private readonly SelectionMenu menu = new();
    private readonly DesignSurface surface = new("Player");
    private NativePlayerInput? input;
    internal void RequestInput(string action, double? value) => ActionRequested?.Invoke(action, value);
    public PlayerChrome()
    {
        Focusable = true;
        Loaded += (_, _) => input ??= new NativePlayerInput(this);
        Unloaded += (_, _) => { input?.Dispose(); input = null; };
        Content = surface; surface.ConfigureHost(this);
        surface.EventRequested += e =>
        {
            if (e.Action == "dismissSelection") SelectionDismissed?.Invoke();
            else Presentation.Dispatch(e.Action, e.Value is null ? null : double.TryParse(e.Value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var parsed) ? parsed : double.NaN, (a, v) => ActionRequested?.Invoke(a, v));
        };
        menu.SelectionRequested += request => SelectionRequested?.Invoke(request);
        Render(new());
    }
    public void Render(PlayerPresentation presentation, string language = "en")
    {
        Presentation = presentation; UiLanguage = language; surface.Slots.Clear(); if (VideoSurface is not null) surface.Slots["video"] = VideoSurface;
        if (SelectionPresentation is { } selection)
        {
            menu.Render(selection, language);
            surface.Slots[selection.Kind + "Selection"] = menu;
        }
        surface.Render(DesignBindings.Player(presentation), language);
    }
}
