using System.Windows;
using System.Windows.Input;

namespace SyncWatch;

// Window presentation outlives each media owner.
internal sealed class WindowFullscreen
{
    private readonly Window window;
    private bool fullscreen;
    private WindowState savedState;
    private WindowStyle savedStyle;
    private ResizeMode savedResizeMode;
    public WindowFullscreen(Window window)
    {
        this.window = window;
        window.KeyDown += (_, e) => { if (!e.Handled && fullscreen && e.Key == Key.Escape) { Toggle(); e.Handled = true; } };
    }
    public void Toggle()
    {
        if (fullscreen)
        {
            window.WindowState = WindowState.Normal;
            window.WindowStyle = savedStyle; window.ResizeMode = savedResizeMode;
            window.WindowState = savedState;
        }
        else
        {
            savedState = window.WindowState; savedStyle = window.WindowStyle; savedResizeMode = window.ResizeMode;
            // Recalculate the maximized bounds after removing the resize frame, including when already maximized.
            window.WindowState = WindowState.Normal;
            window.WindowStyle = WindowStyle.None; window.ResizeMode = ResizeMode.NoResize;
            window.WindowState = WindowState.Maximized;
        }
        fullscreen = !fullscreen;
    }
}
