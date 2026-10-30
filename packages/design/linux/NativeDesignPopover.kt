// SPDX-License-Identifier: Apache-2.0
package syncwatch.design

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.runtime.*
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.awt.ComposePanel
import androidx.compose.ui.awt.LocalAwtWindow
import androidx.compose.ui.awt.RenderSettings
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntRect
import java.awt.AWTEvent
import java.awt.Color
import java.awt.Component
import java.awt.Toolkit
import java.awt.Window
import java.awt.event.*
import javax.swing.JWindow
import javax.swing.SwingUtilities
import kotlin.math.roundToInt

/** An owned popup above heavyweight media; content still measures its shared document. */
@OptIn(ExperimentalComposeUiApi::class)
@Composable internal fun NativeDesignPopover(
    anchor: IntRect, edge: String, onDismiss: () -> Unit, onReturnFocus: () -> Unit = {},
    content: @Composable () -> Unit,
) {
    val owner = LocalAwtWindow.current ?: return
    if (anchor.width <= 0 || anchor.height <= 0) return
    val density = LocalDensity.current.density
    val currentContent by rememberUpdatedState(content)
    val currentDismiss by rememberUpdatedState(onDismiss)
    val currentReturnFocus by rememberUpdatedState(onReturnFocus)
    val reference = remember(owner) { arrayOfNulls<NativePopoverWindow>(1) }
    DisposableEffect(owner) {
        val popup = NativePopoverWindow(owner, { currentDismiss() }, { currentReturnFocus() }) { currentContent() }
        reference[0] = popup
        try {
            popup.open(anchor, density, edge)
        } catch (failure: Throwable) {
            reference[0] = null
            popup.close()
            throw failure
        }
        onDispose {
            reference[0] = null
            popup.close()
        }
    }
    SideEffect { reference[0]?.place(anchor, density, edge) }
}

@OptIn(ExperimentalComposeUiApi::class)
private class NativePopoverWindow(
    private val owner: Window, private val onDismiss: () -> Unit, private val onReturnFocus: () -> Unit,
    content: @Composable () -> Unit,
) {
    private val returnFocus = owner.focusOwner
    private val window = JWindow(owner)
    // Swing graphics paints alpha through the native compositor. A second Skia
    // surface inside a transparent Linux toolkit layer can instead remain black.
    private val panel = try { ComposePanel(renderSettings = RenderSettings.SwingGraphics()) }
        catch (failure: Throwable) { window.dispose(); throw failure }
    private var closed = false
    private var dismissed = false
    private var restoreAfterDismiss = false
    private var anchor = IntRect.Zero
    private var density = 1f
    private var edge = ""
    private fun dismiss(restoreFocus: Boolean = false) {
        if (!closed && !dismissed) {
            restoreAfterDismiss = restoreFocus
            dismissed = true
            onDismiss()
        }
    }
    private val input = AWTEventListener { event ->
        val component = event.source as? Component
        val inside = component != null && SwingUtilities.isDescendingFrom(component, window)
        if (event is MouseEvent && event.id == MouseEvent.MOUSE_PRESSED && !inside) dismiss()
        if (event is KeyEvent && event.id == KeyEvent.KEY_PRESSED && event.keyCode == KeyEvent.VK_ESCAPE && inside) {
            dismiss(restoreFocus = true)
            event.consume()
        }
    }
    private val focus = object : WindowAdapter() {
        override fun windowLostFocus(event: WindowEvent) = dismiss()
        override fun windowClosed(event: WindowEvent) = dismiss()
    }
    private val movement = object : ComponentAdapter() {
        override fun componentMoved(event: ComponentEvent) = place(anchor, density, edge)
        override fun componentResized(event: ComponentEvent) = dismiss()
        override fun componentHidden(event: ComponentEvent) = dismiss()
    }

    init {
        try {
            window.type = Window.Type.POPUP
            window.focusableWindowState = true
            window.background = Color(0, 0, 0, 0)
            panel.isOpaque = false
            panel.background = Color(0, 0, 0, 0)
            panel.setContent {
                val scale = LocalDensity.current.density
                val focusManager = LocalFocusManager.current
                LaunchedEffect(Unit) {
                    // AWT can deliver focus before the first Compose focus targets exist.
                    // Wait for that content, then establish keyboard ownership once.
                    withFrameNanos { }
                    if (!closed && window.isShowing) {
                        window.requestFocus()
                        panel.requestFocusInWindow()
                        focusManager.moveFocus(FocusDirection.Next)
                    }
                }
                Box(Modifier.wrapContentSize(Alignment.TopStart, unbounded = true).onSizeChanged { size ->
                    if (!closed) {
                        val width = (size.width / scale).roundToInt()
                        val height = (size.height / scale).roundToInt()
                        if (width != window.width || height != window.height) window.setSize(width, height)
                        place(anchor, density, edge)
                    }
                }) { content() }
            }
            window.contentPane.add(panel)
        } catch (failure: Throwable) { close(); throw failure }
    }

    fun open(anchor: IntRect, density: Float, edge: String) {
        this.anchor = anchor
        this.density = density
        this.edge = edge
        window.pack()
        place(anchor, density, edge)
        window.addWindowFocusListener(focus)
        owner.addComponentListener(movement)
        owner.addWindowListener(focus)
        Toolkit.getDefaultToolkit().addAWTEventListener(input, AWTEvent.MOUSE_EVENT_MASK or AWTEvent.KEY_EVENT_MASK)
        window.isVisible = true
        panel.requestFocusInWindow()
    }

    fun place(anchor: IntRect, density: Float, edge: String) {
        if (closed || !owner.isShowing) return
        this.anchor = anchor
        this.density = density
        this.edge = edge
        val origin = owner.locationOnScreen
        val insets = owner.insets
        val width = owner.width - insets.left - insets.right
        val height = owner.height - insets.top - insets.bottom
        val x = ((anchor.right / density).roundToInt() - window.width).coerceIn(0, (width - window.width).coerceAtLeast(0))
        val target = if (edge == "top") (anchor.top / density).roundToInt() - window.height else (anchor.bottom / density).roundToInt()
        val y = target.coerceIn(0, (height - window.height).coerceAtLeast(0))
        window.setLocation(origin.x + insets.left + x, origin.y + insets.top + y)
    }

    fun close() {
        if (closed) return
        val restoreFocus = restoreAfterDismiss || window.isFocused
        closed = true
        Toolkit.getDefaultToolkit().removeAWTEventListener(input)
        owner.removeComponentListener(movement)
        owner.removeWindowListener(focus)
        window.removeWindowFocusListener(focus)
        try {
            window.contentPane.remove(panel)
            panel.dispose()
        } finally {
            window.dispose()
            // A newly opened dialog/popup owns input; never take focus away from it.
            if (restoreFocus && owner.isShowing && owner.ownedWindows.none { it.isShowing && it.isFocusableWindow }) {
                // Requesting the Window itself strands keys on ComposeWindow
                // when selection has already disposed the former anchor.
                // Its original canvas survives a content/media replacement.
                returnFocus?.takeIf { it.isShowing && SwingUtilities.isDescendingFrom(it, owner) }
                    // A focusable popup may still own the native window focus here.
                    // Request the original component together with its owner window;
                    // requestFocusInWindow cannot restore an unfocused owner.
                    ?.requestFocus()
                onReturnFocus()
            }
        }
    }
}
