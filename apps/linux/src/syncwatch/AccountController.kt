package syncwatch

import androidx.compose.runtime.*
import kotlinx.coroutines.*
import syncwatch.design.AccountPresentation
import syncwatch.design.LibraryPresentation
import syncwatch.room.*

/** Window-owned account work; no account failure changes movie or synchronization state. */
internal class AccountController(private val scope: CoroutineScope, private val store: AccountSessionStore? = null) {
    var presentation by mutableStateOf(AccountPresentation()); private set
    private var saved: AccountSavedSession? = null
    private var memoryOnly = store == null
    private var memoryNotice = "accountMemoryOnly"
    private var restoring = false
    private var initialized = false
    private var storageWork = false
    private var client: AccountClient? = null
    private var attempt: AccountSignIn? = null
    private var device: AccountDeviceSession? = null
    private var work: Job? = null
    private var refresh: Job? = null
    private var generation = 0L
    init { if (!memoryOnly) presentation = presentation.copy(state = "working", status = "accountRestoring", storageNotice = "accountWillRemember") }
    fun restore(retry: Boolean = false) {
        val persistence = store ?: return
        if (device != null || attempt != null || restoring || initialized && !retry) return
        initialized = true; restoring = true; val current = ++generation
        presentation = presentation.copy(state = "working", status = "accountRestoring")
        work = scope.launch {
            try {
                client?.close(); client = null
                val record = persistence.read()
                if (generation != current) return@launch
                if (record == null) { saved = null; memoryOnly = false; reset(); return@launch }
                saved = record; presentation = presentation.copy(service = record.service)
                val owner = AccountClient(RoomAddress(record.service)); client = owner
                val result = owner.resume(record)
                if (generation != current) return@launch
                checkpoint(result, owner)
                if (generation == current) activate(result, owner)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: RoomProtocolException) {
                if (error.code == "accountSignInRequired") {
                    client?.close(); client = null
                    try { persistence.forget(); saved = null; reset(); presentation = presentation.copy(status = "accountSignInRequired") }
                    catch (_: Exception) { blockStorage() }
                } else { client?.close(); client = null; blockStorage() }
            } catch (_: Exception) { if (generation == current) { client?.close(); client = null; blockStorage() } }
            finally { restoring = false }
        }
    }
    fun libraryPresentation(source: LibraryPresentation) = source.copy(enabledActions = source.enabledActions + "openAccount")
    fun action(action: String, value: String?) {
        when (action) {
            "accountRetryStorage" -> if (presentation.storageBlocked) restore(retry = true)
            "accountUseMemory" -> if (presentation.storageBlocked && device == null) { client?.close(); client = null; memoryOnly = true; memoryNotice = "accountMemoryWithSaved"; saved = null; reset() }
            "accountForgetStorage" -> if (presentation.storageBlocked && device == null && store != null) {
                storageWork = true; presentation = presentation.copy(state = "working", status = "accountWorking")
                work = scope.launch {
                    try { store.forget(); saved = null; memoryOnly = false; reset() }
                    catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) { blockStorage() }
                    finally { storageWork = false }
                }
            }
            "editAccountService" -> if (!presentation.storageBlocked && presentation.state in setOf("signedOut", "failed") && value != null && value.length <= 256) presentation = presentation.copy(service = value)
            "editAccountBrowserProof" -> if (presentation.state == "browserConfirmationRequired" && value != null && value.length <= 32) presentation = presentation.copy(browserProof = value)
            "accountSignIn" -> start()
            "accountOpenBrowser" -> if (presentation.state == "pending") attempt?.let {
                try { java.awt.Desktop.getDesktop().browse(it.browserUri); presentation = presentation.copy(state = "authorizing") }
                catch (_: Exception) { presentation = presentation.copy(status = "accountBrowserUnavailable") }
            }
            "accountVerifyBrowserProof" -> if (presentation.state == "browserConfirmationRequired") verifyBrowser()
            "accountConfirm" -> if (presentation.state == "confirmAccount") confirm()
            "accountCancel" -> {
                presentation = presentation.copy(state = "working", status = "accountWorking")
                generation++; val previous = work; previous?.cancel(); work = scope.launch { previous?.join(); cancel() }
            }
            "accountSignOut" -> if (presentation.state == "signedIn") signOut()
        }
    }
    private fun start() {
        if (presentation.storageBlocked || presentation.state !in setOf("signedOut", "failed") || attempt != null) return
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
                    if (result.state == "browserConfirmationRequired") { presentation = presentation.copy(state = "browserConfirmationRequired", status = "accountBrowserProofRequired"); return@launch }
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
    private fun verifyBrowser() {
        val owner = client ?: return; val signIn = attempt ?: return; val current = generation
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            try {
                val account = owner.verifyBrowser(signIn, presentation.browserProof)
                if (generation != current) return@launch
                presentation = presentation.copy(state = "confirmAccount", status = "accountConfirmAccount", name = account.name, email = account.email)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { if (generation == current) presentation = presentation.copy(state = "browserConfirmationRequired", status = "accountConfirmationFailed") }
        }
    }
    private fun confirm() {
        val owner = client ?: return; val signIn = attempt ?: return; val current = generation
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            try {
                val session = owner.confirm(signIn, presentation.browserProof)
                if (generation != current) { owner.logout(session); return@launch }
                try { checkpoint(session, owner) }
                catch (_: CheckpointException) { cancel(); blockStorage(); return@launch }
                if (generation != current) { withContext(NonCancellable) { owner.logout(session); if (!memoryOnly) store?.forget() }; return@launch }
                attempt = null; activate(session, owner)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { if (generation == current) presentation = presentation.copy(state = "confirmAccount", status = "accountConfirmationFailed") }
        }
    }
    private class CheckpointException : Exception()
    private suspend fun checkpoint(session: AccountDeviceSession, owner: AccountClient) = withContext(NonCancellable) {
        val record = owner.checkpoint(session)
        try { if (!memoryOnly) requireNotNull(store).save(record) }
        catch (_: Exception) {
            try { owner.logout(session) } catch (_: Exception) { }
            try { store?.forget() } catch (_: Exception) { }
            saved = null; device = null; throw CheckpointException()
        }
        saved = record
    }
    private fun activate(session: AccountDeviceSession, owner: AccountClient) {
        device = session
        presentation = presentation.copy(state = "signedIn", status = "accountSignedIn", browserProof = "", name = session.account.name,
            email = session.account.email, storageBlocked = false, storageNotice = if (memoryOnly) memoryNotice else "accountSavedOnDevice")
        val current = generation
        refresh = scope.launch {
            var wait = 480000L
            while (isActive) {
                try {
                    delay(wait); val record = saved ?: return@launch
                    val result = owner.resume(record)
                    if (generation != current) return@launch
                    checkpoint(result, owner)
                    if (generation != current) return@launch
                    device = result; wait = 480000L; presentation = presentation.copy(status = "accountSignedIn")
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: CheckpointException) { owner.close(); client = null; blockStorage(); return@launch }
                catch (error: RoomProtocolException) {
                    if (error.code == "accountSignInRequired") {
                        device = null; saved = null
                        owner.close(); client = null
                        try { if (!memoryOnly) store?.forget(); reset(); presentation = presentation.copy(status = "accountSignInRequired") }
                        catch (_: Exception) { blockStorage() }; return@launch
                    }
                    wait = 15000; presentation = presentation.copy(status = "accountUnavailable")
                } catch (_: Exception) { ensureActive(); wait = 15000; presentation = presentation.copy(status = "accountUnavailable") }
            }
        }
    }
    private fun signOut() {
        val owner = client ?: return; val current = generation
        presentation = presentation.copy(state = "working", status = "accountWorking")
        work = scope.launch {
            refresh?.cancelAndJoin(); refresh = null
            val session = device ?: run { blockStorage(); return@launch }
            presentation = presentation.copy(status = "accountWorking")
            try { owner.logout(session); device = null; if (!memoryOnly) store?.forget(); saved = null; owner.close(); client = null; reset() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { if (device == null) { owner.close(); client = null; blockStorage() } else { if (generation == current) activate(session, owner); presentation = presentation.copy(status = "accountSignOutFailed") } }
        }
    }
    suspend fun dismiss() {
        if (attempt != null || presentation.state == "working" && device == null) {
            val wasStorage = restoring || storageWork; generation++; work?.cancelAndJoin()
            val blocked = presentation.storageBlocked; cancel(); if (wasStorage || blocked) blockStorage()
        }
    }
    suspend fun close() {
        generation++; work?.cancelAndJoin(); refresh?.cancelAndJoin()
        val owner = client; val session = device
        if (memoryOnly && owner != null && session != null) { try { owner.logout(session) } catch (_: Exception) { } }
        device = null; cancel(); saved = null; store?.close()
    }
    private suspend fun cancel() {
        val owner = client; val signIn = attempt
        if (owner != null && signIn != null) { try { owner.cancel(signIn) } catch (_: Exception) { } }
        if (signIn != null && saved != null && !memoryOnly) { try { store?.forget() } catch (_: Exception) { } }
        attempt = null; owner?.close(); client = null; reset()
    }
    private fun reset() { presentation = AccountPresentation(service = presentation.service, storageNotice = if (memoryOnly) memoryNotice else "accountWillRemember") }
    private fun blockStorage() { device = null; presentation = presentation.copy(state = "failed", status = "accountStorageUnavailable", storageBlocked = true, storageNotice = "accountSavedUnavailable", name = "", email = "", browserProof = "") }
    private fun fail() { presentation = presentation.copy(state = "failed", status = "accountFailed") }
}
