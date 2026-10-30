import AppKit
import SwiftUI

/// A player-owned local monitor also receives input over the native VLC drawable.
struct NativePlayerInput: NSViewRepresentable {
    var presentation: PlayerPresentation
    var protectedFocus: Bool
    var onAction: (String, Double?) -> Void

    final class InputView: NSView {
        var presentation = PlayerPresentation()
        var protectedFocus = true
        var onAction: (String, Double?) -> Void = { _, _ in }
        var monitor: Any?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            removeMonitor()
            guard window != nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .scrollWheel]) { [weak self] event in
                guard let self, let window = self.window, event.window === window,
                    window.isKeyWindow, window.attachedSheet == nil, !self.isHiddenOrHasHiddenAncestor
                else { return event }
                let modified = !event.modifierFlags.intersection([.command, .control, .option, .shift]).isEmpty
                if event.type == .scrollWheel {
                    guard self.bounds.contains(self.convert(event.locationInWindow, from: nil)) else { return event }
                    return self.presentation.dispatchWheel(
                        event.scrollingDeltaY, modified: modified, protectedFocus: self.protectedFocus,
                        to: self.onAction) ? nil : event
                }
                let responder = window.firstResponder
                let editing =
                    responder is NSTextView || responder is NSTextField || responder is NSSlider
                    || responder is NSPopUpButton || responder is NSComboBox
                    || (event.keyCode == 49 && responder is NSButton)
                let key: String
                switch event.keyCode {
                case 49: key = "Space"
                case 123: key = "ArrowLeft"
                case 124: key = "ArrowRight"
                case 126: key = "ArrowUp"
                case 125: key = "ArrowDown"
                case 46: key = "KeyM"
                case 3: key = "KeyF"
                case 53: key = "Escape"
                default: return event
                }
                return self.presentation.dispatchKey(
                    key, repeating: event.isARepeat, fullscreen: window.styleMask.contains(.fullScreen),
                    modified: modified, protectedFocus: self.protectedFocus || editing,
                    to: self.onAction) ? nil : event
            }
        }
        func removeMonitor() {
            if let monitor {
                NSEvent.removeMonitor(monitor)
                self.monitor = nil
            }
        }
    }
    func makeNSView(context: Context) -> InputView { InputView(frame: .zero) }
    func updateNSView(_ view: InputView, context: Context) {
        view.presentation = presentation
        view.protectedFocus = protectedFocus
        view.onAction = onAction
    }
    static func dismantleNSView(_ view: InputView, coordinator: ()) {
        view.removeMonitor()
        view.onAction = { _, _ in }
    }
}
