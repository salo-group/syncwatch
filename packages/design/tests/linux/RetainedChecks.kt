// SPDX-License-Identifier: Apache-2.0
package syncwatch.design.tests

import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.ImageComposeScene
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.awt.ComposePanel
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.use
import java.awt.Canvas
import java.awt.Dimension
import java.awt.KeyboardFocusManager
import java.util.concurrent.atomic.AtomicReference
import javax.swing.JFrame
import javax.swing.SwingUtilities
import syncwatch.design.*
import syncwatch.state.CompositionStateOwner
import syncwatch.state.ProvideCompositionState

internal fun verifyRetainedRendering() {
    var presented by mutableStateOf(true)
    var created = 0
    var disposed = 0
    val owner = CompositionStateOwner(canBeSaved = { saveable(it) })
    owner.resume()
    try {
        ImageComposeScene(800, 600, content = {
            ProvideCompositionState(owner) {
                ApplicationChrome(presented,
                    library = { Box(Modifier.fillMaxSize().background(Color.Yellow)) },
                    player = {
                        DisposableEffect(Unit) { created++; onDispose { disposed++ } }
                        Box(Modifier.fillMaxSize().background(Color.Cyan))
                    })
            }
        }).use { scene ->
            for (active in listOf(true, false, true, false, true)) {
                presented = active
                Snapshot.sendApplyNotifications()
                scene.render().close()
                scene.render().use { image ->
                    val pixel = image.peekPixels()!!.getColor(400, 300)
                    check(pixel == if (active) 0xff00ffff.toInt() else 0xffffff00.toInt()) { "retained pixels: $active ${pixel.toUInt().toString(16)}" }
                }
                check(created == 1 && disposed == 0) { "navigation disposed retained content" }
            }
        }
        check(disposed == 1) { "final removal did not dispose content" }
    } finally { owner.close() }
}

/** An offscreen scene verifies focus lifetime without synthesizing desktop input. */
private fun verifyRetainedFocus() {
    var presented by mutableStateOf(true)
    var focused = false
    var disabledFocused = false
    val requester = FocusRequester()
    val disabledRequester = FocusRequester()
    ImageComposeScene(800, 600, content = {
        RetainedContent(presented, Modifier.fillMaxSize()) {
            Column {
                Box(Modifier.focusRequester(requester).onFocusChanged { focused = it.isFocused }.focusable())
                Box(Modifier.focusRequester(disabledRequester).onFocusChanged { disabledFocused = it.isFocused }
                    .focusProperties { canFocus = false }.focusable())
            }
            LaunchedEffect(presented) { if (presented) requester.requestFocus() }
        }
    }).use { scene ->
        for (active in listOf(true, false, true, false)) {
            presented = active
            Snapshot.sendApplyNotifications()
            repeat(4) { scene.render().close() }
            check(focused == active) { "inactive retained content kept keyboard focus: $active $focused" }
            disabledRequester.requestFocus()
            repeat(2) { scene.render().close() }
            check(!disabledFocused) { "retained content overrode a child's focus restriction" }
            if (!active) {
                requester.requestFocus()
                repeat(2) { scene.render().close() }
                check(!focused) { "inactive retained content accepted new keyboard focus" }
            }
        }
    }
}

/** An application-owned real window checks heavyweight peer lifetime, not manual UI acceptance. */
private fun verifyRetainedNativePeer() {
    var presented by mutableStateOf(true)
    var added = 0
    var removed = 0
    val canvas = object : Canvas() {
        override fun addNotify() { super.addNotify(); added++ }
        override fun removeNotify() { removed++; super.removeNotify() }
    }
    lateinit var window: JFrame
    lateinit var panel: ComposePanel
    lateinit var owner: CompositionStateOwner
    fun onEvent(action: () -> Unit) {
        val error = AtomicReference<Throwable?>()
        SwingUtilities.invokeAndWait { try { action() } catch (failure: Throwable) { error.set(failure) } }
        error.get()?.let { throw it }
    }
    fun waitFor(predicate: () -> Boolean) {
        val deadline = System.nanoTime() + 15_000_000_000L
        while (true) {
            var ready = false
            onEvent { ready = predicate() }
            if (ready) return
            check(System.nanoTime() < deadline) { "retained native peer did not settle" }
            Thread.sleep(20)
        }
    }
    onEvent {
        owner = CompositionStateOwner(canBeSaved = { saveable(it) })
        owner.resume()
        canvas.minimumSize = Dimension(0, 0)
        canvas.preferredSize = Dimension(0, 0)
        panel = ComposePanel().apply {
            setContent {
                ProvideCompositionState(owner) {
                    ApplicationChrome(presented, library = { LibraryWorkspace() }, player = { NativePlayerSurface(canvas) })
                }
            }
        }
        window = JFrame("SyncWatch retained surface check").apply {
            defaultCloseOperation = JFrame.DISPOSE_ON_CLOSE
            contentPane.add(panel)
            setSize(800, 600)
            isVisible = true
        }
    }
    try {
        waitFor { canvas.isShowing && canvas.width > 0 && canvas.height > 0 }
        for (active in listOf(false, true, false, true)) {
            onEvent { presented = active; Snapshot.sendApplyNotifications() }
            waitFor { canvas.isShowing == active }
            onEvent {
                check(canvas.isDisplayable && added == 1 && removed == 0) { "navigation replaced the native peer" }
                check(canvas.width > 0 && canvas.height > 0) { "navigation lost native geometry" }
                window.setSize(if (active) 896 else 800, 600)
            }
            waitFor { canvas.isShowing == active }
        }
    } finally {
        onEvent { window.dispose(); owner.close() }
    }
    onEvent { check(added == 1 && removed == 1) { "native peer did not release once on final close" } }
}

/** Component lifecycle only: no synthesized keys, mouse events or control invocation. */
private fun verifyNativePopoverFocusReturn() {
    var open by mutableStateOf(false)
    lateinit var window: JFrame
    val manager = KeyboardFocusManager.getCurrentKeyboardFocusManager()
    var original: java.awt.Component? = null
    fun onEvent(action: () -> Unit) = SwingUtilities.invokeAndWait(action)
    fun waitFor(message: String, predicate: () -> Boolean) {
        val end = System.nanoTime() + 10_000_000_000L
        while (true) {
            var ready = false
            onEvent { ready = predicate() }
            if (ready) return
            check(System.nanoTime() < end) { message }
            Thread.sleep(20)
        }
    }
    onEvent {
        val panel = ComposePanel()
        panel.setContent {
            Box(Modifier.fillMaxSize().focusable()) {
                if (open) NativeDesignPopover(IntRect(20, 100, 80, 140), "top", onDismiss = { open = false }) {
                    // No return callback: a replaced media scope no longer owns its old anchor.
                    Box(Modifier.size(120.dp, 60.dp).focusable())
                }
            }
        }
        window = JFrame("SyncWatch popup focus lifecycle check").apply {
            defaultCloseOperation = JFrame.DISPOSE_ON_CLOSE
            contentPane.add(panel); setSize(800, 600); isVisible = true
        }
    }
    try {
        waitFor("initial canvas did not receive native focus") {
            val component = manager.focusOwner
            if (component != null && component !== window && SwingUtilities.isDescendingFrom(component, window)) {
                original = component; true
            } else false
        }
        onEvent { open = true; Snapshot.sendApplyNotifications() }
        waitFor("popup did not acquire native focus") { window.ownedWindows.any { it.isFocused } }
        onEvent { open = false; Snapshot.sendApplyNotifications() }
        waitFor("popup close stranded keyboard focus on the owner Window") { window.isFocused && manager.focusOwner === original }
    } finally { onEvent { window.dispose() } }
}

fun main(arguments: Array<String>) {
    if ("--native" in arguments) { verifyRetainedNativePeer(); verifyNativePopoverFocusReturn() }
    else SwingUtilities.invokeAndWait { verifyRetainedRendering(); verifyRetainedFocus() }
    println("SYNCWATCH_RETAINED_CONTENT_OK: presentation preserves content; final disposal releases it")
}
