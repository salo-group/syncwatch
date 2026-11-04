package syncwatch

import androidx.compose.runtime.*
import kotlinx.coroutines.*
import syncwatch.design.AccountPresentation
import syncwatch.design.LibraryPresentation
import syncwatch.room.*

/** Window-owned account work; no account failure changes movie or synchronization state. */
internal class AccountController(private val scope: CoroutineScope) {
    var presentation by mutableStateOf(AccountPresentation()); private set
    private var client: AccountClient? = null
    private var attempt: AccountSignIn? = null
    private var device: AccountDeviceSession? = null
    private var work: Job? = null
    private var refresh: Job? = null
    private var generation = 0L
    fun libraryPresentation(source: LibraryPresentation) = source.copy(enabledActions = source.enabledActions + "openAccount")
    fun action(action: String, value: String?) {
        when (action) {
            "editAccountService" -> if (presentation.state in setOf("signedOut", "failed") && value != null && value.length <= 256) presentation = presentation.copy(service = value)
            "accountSignIn" -> start()
            "accountOpenBrowser" -> if (presentation.state == "pending") attempt?.let {
                try { java.awt.Desktop.getDesktop().browse(it.browserUri); presentation = presentation.copy(state = "authorizing") }
                catch (_: Exception) { presentation = presentation.copy(status = "accountFailed") }
            }
            "accountConfirm" -> if (presentation.state == "confirmAccount") confirm()
            "accountCancel" -> { generation++; val previous = work; previous?.cancel(); work = scope.launch { previous?.join(); cancel() } }
            "accountSignOut" -> if (presentation.state == "signedIn") signOut()
        }
    }
    private fun start() {
        if (presentation.state !in setOf("signedOut", "failed") || attempt != null) return
        val owner = try { AccountClient(RoomAddress(presentation.service)) } catch (_: Exception) { fail(); return }
        client = owner; val current = ++generation
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            try {
                val signIn = owner.start()
                if (generation != current) { owner.cancel(signIn); return@launch }
                attempt = signIn; presentation = presentation.copy(state = "pending", status = "accountPending")
                while (true) {
                    delay(2000)
                    val result = owner.poll(signIn)
                    if (generation != current) return@launch
                    if (result.state == "confirmAccount" && result.account != null) {
                        presentation = presentation.copy(state = "confirmAccount", status = "accountConfirmAccount", name = result.account.name, email = result.account.email)
                        return@launch
                    }
                    if (result.state == "authorizing") presentation = presentation.copy(state = "authorizing")
                    if (result.state in setOf("failed", "cancelled", "complete")) { cancel(); fail(); return@launch }
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { if (generation == current) { cancel(); fail() } }
        }
    }
    private fun confirm() {
        val owner = client ?: return; val signIn = attempt ?: return; val current = generation
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            try {
                val session = owner.confirm(signIn)
                if (generation != current) { owner.logout(session); return@launch }
                device = session; attempt = null
                presentation = presentation.copy(state = "signedIn", status = "accountSignedIn", name = session.account.name, email = session.account.email)
                refresh = scope.launch {
                    try { while (true) { delay(480000); device = owner.refresh(device ?: return@launch) } }
                    catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) { presentation = presentation.copy(status = "accountFailed") }
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { if (generation == current) presentation = presentation.copy(state = "confirmAccount", status = "accountFailed") }
        }
    }
    private fun signOut() {
        val owner = client ?: return; val session = device ?: return
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            refresh?.cancelAndJoin(); refresh = null
            try { owner.logout(session); device = null; owner.close(); client = null; reset() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { presentation = presentation.copy(state = "signedIn", status = "accountSignOutFailed") }
        }
    }
    suspend fun dismiss() { if (attempt != null || presentation.state == "working" && device == null) close() }
    suspend fun close() {
        generation++; work?.cancelAndJoin(); refresh?.cancelAndJoin()
        val owner = client; val session = device
        if (owner != null && session != null) { try { owner.logout(session) } catch (_: Exception) { } }
        device = null; cancel()
    }
    private suspend fun cancel() {
        val owner = client; val signIn = attempt
        if (owner != null && signIn != null) { try { owner.cancel(signIn) } catch (_: Exception) { } }
        attempt = null; owner?.close(); client = null; reset()
    }
    private fun reset() { presentation = AccountPresentation(service = presentation.service) }
    private fun fail() { presentation = presentation.copy(state = "failed", status = "accountFailed") }
}
