using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SyncWatch.Design;

internal static class PlayerAssertions
{
    private sealed record Event(string Action, double? Value);
    private static void Check(bool value, string name) { if (!value) throw new InvalidOperationException("Player assertion: " + name); }
    private static IEnumerable<T> Find<T>(DependencyObject root) where T : DependencyObject
    {
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T item) yield return item;
            foreach (var nested in Find<T>(child)) yield return nested;
        }
    }
    private static T Element<T>(DependencyObject root, string id) where T : FrameworkElement => Find<T>(root).Single(e => AutomationProperties.GetAutomationId(e) == id);
    private static void Layout(FrameworkElement element, int width = 800, int height = 600)
    {
        element.Measure(new Size(width, height)); element.Arrange(new Rect(0, 0, width, height)); element.UpdateLayout();
    }
    private static Event ReadEvent(JsonElement value) => new(value.GetProperty("action").GetString()!, value.TryGetProperty("value", out var number) ? number.GetDouble() : null);
    public static void Run(Application application, ResourceDictionary dictionary)
    {
        PlayerInputAssertions.Run();
        using var data = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "player-actions.json")));
        int scenarios = 0;
        foreach (var fixture in data.RootElement.GetProperty("cases").EnumerateArray())
        {
            var p = new PlayerPresentation
            {
                State = Enum.Parse<PlayerVisualState>(fixture.GetProperty("state").GetString()!, true),
                PositionMs = fixture.GetProperty("positionMs").GetInt64(),
                DurationMs = fixture.GetProperty("durationMs").GetInt64(),
                Volume = fixture.GetProperty("volume").GetDouble(),
                SkipSeconds = fixture.GetProperty("skipSeconds").GetInt32(),
                HasPrevious = fixture.GetProperty("hasPrevious").GetBoolean(),
                HasNext = fixture.GetProperty("hasNext").GetBoolean(),
                EnabledActions = fixture.GetProperty("enabledActions").EnumerateArray().Select(a => a.GetString()!).ToHashSet(),
            };
            var events = new List<Event>();
            foreach (var request in fixture.GetProperty("requests").EnumerateArray())
            {
                var e = ReadEvent(request); p.Dispatch(e.Action, e.Value, (action, value) => events.Add(new(action, value)));
            }
            Check(events.SequenceEqual(fixture.GetProperty("expected").EnumerateArray().Select(ReadEvent)), fixture.GetProperty("name").GetString()!);
            Check(p.PositionMs == fixture.GetProperty("positionMs").GetInt64() && p.State == Enum.Parse<PlayerVisualState>(fixture.GetProperty("state").GetString()!, true), "requests cannot acknowledge themselves");
            Check(p.DisplayedPosition >= 0 && p.DisplayedPosition <= p.DisplayedDuration, "invalid observations have safe display bounds"); scenarios++;
        }
        Check(scenarios == 19, "shared corpus coverage");
        var ready = PlayerPresentation.Gallery(PlayerVisualState.Paused);
        int invalidEvents = 0;
        foreach (double value in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
        {
            ready.Dispatch("seek", value, (_, _) => invalidEvents++); ready.Dispatch("setMovieVolume", value, (_, _) => invalidEvents++);
        }
        Check(invalidEvents == 0 && !(ready with { Volume = double.NaN }).Allows("seek"), "nonfinite values");
        Check(PlayerPresentation.Time(754000) == "12:34" && PlayerPresentation.Time(5400000) == "1:30:00" && PlayerPresentation.Time(double.NaN) == "00:00", "time formatting");
        Check(Enum.GetNames<PlayerVisualState>().Select(n => char.ToLowerInvariant(n[0]) + n[1..]).SequenceEqual(SyncWatchPlayer.States), "state contract");
        var view = new PlayerChrome(); var received = new List<Event>(); view.ActionRequested += (a, v) => received.Add(new(a, v));
        view.Render(ready); Layout(view); Check(received.Count == 0, "render emits no input");
        Check(Element<Button>(view, "player.playerSettings").IsEnabled && Element<Button>(view, "player.audioTracks").IsEnabled && Element<Button>(view, "player.subtitles").IsEnabled, "declared local menu capabilities");
        view.Render(ready with { EnabledActions = ready.EnabledActions.Except(["selectAudio", "selectSubtitles"]).ToHashSet() }); Layout(view);
        Check(!Element<Button>(view, "player.audioTracks").IsEnabled && !Element<Button>(view, "player.subtitles").IsEnabled, "local menu capabilities can be revoked");
        view.Render(ready); Layout(view);
        Element<Slider>(view, "player.seek").Value = 5000;
        Element<Button>(view, "player.play").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(received.SequenceEqual(new[] { new Event("seek", 5000), new Event("playPause", null) }) && view.Presentation.PositionMs == ready.PositionMs, "native input requests and immutable observation");
        view.Render(ready with { PositionMs = 2000 }); Layout(view);
        Check(received.Count == 2 && Element<Slider>(view, "player.seek").Value == 2000, "new observation cannot echo a seek");
        Element<Button>(view, "player.skipBackward").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Element<Slider>(view, "player.setMovieVolume").Value = 0.25;
        Check(received[^2] == new Event("seek", 0) && received[^1] == new Event("setMovieVolume", 0.25), "native skip/volume mapping");
        view.Render(PlayerPresentation.Gallery(PlayerVisualState.Loading)); Layout(view); int previous = received.Count;
        Element<Button>(view, "player.play").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(!Element<Slider>(view, "player.seek").IsEnabled && Element<Button>(view, "player.playerSettings").IsEnabled && !Element<Button>(view, "player.audioTracks").IsEnabled && received.Count == previous, "loading rejects transport while local settings remain available");
        view.Render(ready with { EnabledActions = new HashSet<string>() }); Layout(view);
        Element<Button>(view, "player.playerSettings").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(!Element<Button>(view, "player.playerSettings").IsEnabled && received.Count == previous, "settings without a capability cannot dispatch");
        view.Render(ready with { State = PlayerVisualState.Playing }, "ru"); Layout(view);
        Check(AutomationProperties.GetName(Element<Button>(view, "player.pause")) == SyncWatchCopy.Text("pause", "ru"), "localized accessible control name");
        var video = new Border(); view.VideoSurface = video; view.Render(ready); Layout(view); view.Render(ready with { PositionMs = 9000 }); Layout(view);
        Check(VisualTreeHelper.GetParent(video) is Grid && received.Count == previous, "owned video slot is reparented without input");
        string? output = Environment.GetEnvironmentVariable("SYNCWATCH_DESIGN_SCREENSHOTS");
        if (output is not null) Directory.CreateDirectory(output);
        int images = 0;
        foreach (var theme in new[] { "dark", "light" })
        {
            application.Resources.MergedDictionaries.Add((ResourceDictionary)dictionary["theme." + theme]);
            foreach (var state in Enum.GetValues<PlayerVisualState>())
                foreach (var language in new[] { "en", "ru" })
                    foreach (var (width, height) in new[] { (1120, 760), (800, 600) })
                    {
                        var chrome = new PlayerChrome(); chrome.Render(PlayerPresentation.Gallery(state), language); Layout(chrome, width, height);
                        Check(chrome.ActualWidth == width && chrome.ActualHeight == height, "logical viewport");
                        foreach (var button in Find<Button>(chrome))
                        {
                            Check(button.ActualWidth >= SyncWatchTokens.ControlPlayerButtonSize && button.ActualHeight >= SyncWatchTokens.ControlPlayerButtonSize, "control hit target");
                            var origin = button.TransformToAncestor(chrome).Transform(new Point());
                            Check(origin.X >= 0 && origin.Y >= 0 && origin.X + button.ActualWidth <= width && origin.Y + button.ActualHeight <= height, "controls fit minimum viewport");
                        }
                        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32); bitmap.Render(chrome);
                        byte[] pixel = new byte[4]; bitmap.CopyPixels(new Int32Rect(width / 2, height / 2, 1, 1), pixel, 4, 0);
                        Check(pixel.SequenceEqual(new byte[] { 0, 0, 0, 255 }), "video slot is opaque and isolated from chrome theme");
                        if (state == PlayerVisualState.Paused)
                        {
                            int y = height - (int)SyncWatchTokens.LayoutPlayerFooterHeight + (int)SyncWatchTokens.ControlPlayerSliderHeight / 2;
                            bitmap.CopyPixels(new Int32Rect(110, y, 1, 1), pixel, 4, 0);
                            Check(pixel[0] > pixel[2] + 50 && pixel[3] == 255, "rendered timeline uses the shared accent at " + width + "x" + height + ": BGRA=" + string.Join(',', pixel) + ", seek origin=" + Element<Slider>(chrome, "player.seek").TransformToAncestor(chrome).Transform(new Point()) + ", seek size=" + Element<Slider>(chrome, "player.seek").RenderSize);
                        }
                        if (output is not null)
                        {
                            var png = new PngBitmapEncoder(); png.Frames.Add(BitmapFrame.Create(bitmap));
                            using var file = File.Create(Path.Combine(output, $"player{state}-{theme}-{language}-{width}x{height}.png")); png.Save(file);
                        }
                        images++;
                    }
        }
        Console.WriteLine($"Verified {scenarios} shared player scenarios, native input/observation boundary and {images} rendered player images");
    }
}
