using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;

namespace SyncWatch.Design;

// The filter runs before WPF keyboard sinks and includes native child HWND input.
// Its lifetime belongs to the mounted PlayerChrome; protected controls keep native input.
internal sealed class NativePlayerInput : IDisposable
{
    private readonly PlayerChrome chrome;
    private readonly Window? window;
    private readonly nint handle;
    private readonly HwndSource? source;
    internal NativePlayerInput(PlayerChrome chrome)
    {
        this.chrome = chrome; window = Window.GetWindow(chrome);
        handle = window is null ? 0 : new WindowInteropHelper(window).Handle;
        source = handle == 0 ? null : HwndSource.FromHwnd(handle);
        source?.AddHook(WindowMessage);
        ComponentDispatcher.ThreadFilterMessage += Process;
    }
    private bool Ready => window?.IsActive == true && chrome.IsVisible && chrome.SelectionPresentation is null;
    private nint WindowMessage(nint hwnd, int message, nint wParam, nint lParam, ref bool handled)
    {
        if (handled || !Ready) return 0;
        // The disabled VLC child leaves input with the native host; its default wheel route reaches this owner.
        if (message == 0x0210 && (wParam.ToInt64() & 0xffff) == 0x0201) chrome.Focus();
        if (message != 0x020A) return 0;
        var point = new Point(unchecked((short)lParam.ToInt64()), unchecked((short)(lParam.ToInt64() >> 16)));
        point = chrome.PointFromScreen(point);
        if (!new Rect(chrome.RenderSize).Contains(point)) return 0;
        handled = chrome.Presentation.DispatchWheel(unchecked((short)(wParam.ToInt64() >> 16)), chrome.RequestInput,
            (Keyboard.Modifiers & (ModifierKeys.Alt | ModifierKeys.Control | ModifierKeys.Shift | ModifierKeys.Windows)) != 0);
        return 0;
    }
    private void Process(ref MSG message, ref bool handled)
    {
        if (handled || handle == 0 || !Ready
            || message.hwnd != handle && GetAncestor(message.hwnd, 2) != handle) return;
        bool modified = (Keyboard.Modifiers & (ModifierKeys.Alt | ModifierKeys.Control | ModifierKeys.Shift | ModifierKeys.Windows)) != 0;
        string? key = InputKey(message.message, message.wParam.ToInt64());
        if (key is null) return;
        bool editing = Keyboard.FocusedElement is TextBoxBase or PasswordBox or Slider or ComboBox
            || key == "Space" && Keyboard.FocusedElement is ButtonBase;
        handled = chrome.Presentation.DispatchKey(key, chrome.RequestInput,
            repeating: (message.lParam.ToInt64() & (1L << 30)) != 0,
            fullscreen: window is { WindowStyle: WindowStyle.None, WindowState: WindowState.Maximized },
            modified: modified, protectedFocus: editing);
    }
    internal static string? InputKey(int message, long value)
    {
        // Unicode keyboard transports deliver WM_CHAR through VK_PACKET. A consumed
        // WM_KEYDOWN is never translated, so physical keys cannot dispatch twice.
        if (message == 0x0102) return value switch
        {
            ' ' => "Space",
            'm' or 'M' => "KeyM",
            'f' or 'F' => "KeyF",
            _ => null,
        };
        if (message != 0x0100) return null;
        return value switch
        {
            0x20 => "Space",
            0x25 => "ArrowLeft",
            0x27 => "ArrowRight",
            0x26 => "ArrowUp",
            0x28 => "ArrowDown",
            0x4D => "KeyM",
            0x46 => "KeyF",
            0x1B => "Escape",
            _ => null,
        };
    }
    public void Dispose()
    {
        ComponentDispatcher.ThreadFilterMessage -= Process;
        source?.RemoveHook(WindowMessage);
    }
    [DllImport("user32.dll")]
    private static extern nint GetAncestor(nint window, uint flags);
}
