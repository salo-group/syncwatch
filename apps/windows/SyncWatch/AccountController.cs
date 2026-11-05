using System.Diagnostics;
using SyncWatch.Room;

namespace SyncWatch;

/// <summary>Window-owned account work, isolated from movie and room lifetimes.</summary>
internal sealed class AccountController
{
    public Design.AccountPresentation Presentation { get; private set; } = new();
    public event Action? Changed;
    private readonly AccountSessionStore? store;
    private AccountSavedSession? saved;
    private bool memoryOnly, restoring, initialized, storageWork;
    private string memoryNotice = "accountMemoryOnly";
    private AccountClient? client;
    private AccountSignIn? attempt;
    private AccountDeviceSession? device;
    private CancellationTokenSource stop = new();
    private CancellationTokenSource refreshStop = new();
    private Task work = Task.CompletedTask, refresh = Task.CompletedTask;
    private Task closing = Task.CompletedTask;
    private long generation;
    public AccountController(string? directory = null)
    {
        store = directory is null ? null : new(directory); memoryOnly = store is null;
        if (!memoryOnly) { Presentation.State = "working"; Presentation.Status = "accountRestoring"; Presentation.StorageNotice = "accountWillRemember"; }
    }
    public void Restore(bool retry = false)
    {
        if (store is null || device is not null || attempt is not null || restoring || initialized && !retry) return;
        initialized = true; restoring = true; long current = ++generation;
        stop.Dispose(); stop = new(); Presentation.State = "working"; Presentation.Status = "accountRestoring"; Changed?.Invoke();
        work = RestoreCore(current, stop.Token);
    }
    private async Task RestoreCore(long current, CancellationToken cancellation)
    {
        try
        {
            client?.Dispose(); client = null;
            AccountSavedSession? record = await Task.Run(() => store!.Read(), cancellation);
            if (generation != current) return;
            if (record is null) { saved = null; memoryOnly = false; Reset(); return; }
            saved = record; Presentation.Service = record.Service;
            var owner = new AccountClient(new RoomAddress(record.Service)); client = owner;
            AccountDeviceSession result = await owner.ResumeAsync(record, cancellation);
            if (generation != current) return;
            await Checkpoint(result, owner);
            if (generation == current) Activate(result, owner);
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (RoomProtocolException error) when (error.Message == "accountSignInRequired")
        {
            client?.Dispose(); client = null;
            try { await Task.Run(() => store!.Forget()); saved = null; Reset(); Presentation.Status = "accountSignInRequired"; Changed?.Invoke(); }
            catch (Exception) { BlockStorage(); }
        }
        catch (Exception) { if (generation == current) { client?.Dispose(); client = null; BlockStorage(); } }
        finally { restoring = false; }
    }
    public Design.LibraryPresentation LibraryPresentation(Design.LibraryPresentation source)
    {
        return source with { EnabledActions = new HashSet<string>(source.EnabledActions) { "openAccount" } };
    }
    public void Action(string action, string? value)
    {
        if (action == "accountRetryStorage" && Presentation.StorageBlocked) Restore(retry: true);
        else if (action == "accountUseMemory" && Presentation.StorageBlocked && device is null)
        { client?.Dispose(); client = null; memoryOnly = true; memoryNotice = "accountMemoryWithSaved"; saved = null; Reset(); }
        else if (action == "accountForgetStorage" && Presentation.StorageBlocked && device is null) work = ForgetStorage();
        else if (action == "editAccountService" && !Presentation.StorageBlocked && Presentation.State is "signedOut" or "failed" && value is { Length: <= 256 }) { Presentation.Service = value; Changed?.Invoke(); }
        else if (action == "editAccountBrowserProof" && Presentation.State == "browserConfirmationRequired" && value is { Length: <= 32 }) { Presentation.BrowserProof = value; Changed?.Invoke(); }
        else if (action == "accountSignIn") Start();
        else if (action == "accountOpenBrowser" && attempt is not null && Presentation.State == "pending")
        {
            try
            {
                using Process? process = Process.Start(new ProcessStartInfo(attempt.BrowserUri.AbsoluteUri) { UseShellExecute = true });
                Presentation.State = "authorizing";
            }
            catch (Exception error) when (error is System.ComponentModel.Win32Exception or InvalidOperationException) { Presentation.Status = "accountBrowserUnavailable"; }
            Changed?.Invoke();
        }
        else if (action == "accountVerifyBrowserProof" && Presentation.State == "browserConfirmationRequired") work = VerifyBrowser();
        else if (action == "accountConfirm" && Presentation.State == "confirmAccount") work = Confirm();
        else if (action == "accountCancel")
        {
            Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
            generation++; stop.Cancel(); Task previous = work;
            work = CancelAfter(previous);
        }
        else if (action == "accountSignOut" && Presentation.State == "signedIn") work = SignOut();
    }
    private void Start()
    {
        if (Presentation.StorageBlocked || Presentation.State is not ("signedOut" or "failed") || attempt is not null) return;
        try { client = new AccountClient(new RoomAddress(Presentation.Service)); }
        catch (RoomProtocolException) { Fail(); return; }
        stop.Dispose(); stop = new(); long scope = ++generation;
        Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        work = Poll(client, scope, stop.Token);
    }
    private async Task Poll(AccountClient owner, long scope, CancellationToken cancellation)
    {
        try
        {
            AccountSignIn signIn = await owner.StartAsync(cancellation);
            if (generation != scope) { await owner.CancelAsync(signIn); return; }
            attempt = signIn; Presentation.State = "pending"; Presentation.Status = "accountPending"; Changed?.Invoke();
            while (true)
            {
                await Task.Delay(TimeSpan.FromSeconds(2), cancellation);
                AccountSignInStatus status = await owner.PollAsync(signIn, cancellation);
                if (generation != scope) return;
                if (status.State == "browserConfirmationRequired") { Presentation.State = "browserConfirmationRequired"; Presentation.Status = "accountBrowserProofRequired"; Changed?.Invoke(); return; }
                if (status.State == "confirmAccount" && status.Account is AccountProfile profile)
                {
                    Presentation.State = "confirmAccount"; Presentation.Status = "accountConfirmAccount";
                    Presentation.Name = profile.Name; Presentation.Email = profile.Email; Changed?.Invoke(); return;
                }
                if (status.State == "authorizing") { Presentation.State = "authorizing"; Changed?.Invoke(); }
                if (status.State is "failed" or "cancelled" or "complete") { await Cancel(); Fail(); return; }
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception) { if (generation == scope) { await Cancel(); Fail(); } }
    }
    private async Task VerifyBrowser()
    {
        if (client is not AccountClient owner || attempt is not AccountSignIn signIn) return;
        long current = generation; Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        try
        {
            AccountProfile profile = await owner.VerifyBrowserAsync(signIn, Presentation.BrowserProof, stop.Token);
            if (generation != current) return;
            Presentation.State = "confirmAccount"; Presentation.Status = "accountConfirmAccount";
            Presentation.Name = profile.Name; Presentation.Email = profile.Email; Changed?.Invoke();
        }
        catch (Exception) { if (generation == current) { Presentation.State = "browserConfirmationRequired"; Presentation.Status = "accountConfirmationFailed"; Changed?.Invoke(); } }
    }
    private async Task Confirm()
    {
        if (client is not AccountClient owner || attempt is not AccountSignIn signIn) return;
        long scope = generation; Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        try
        {
            AccountDeviceSession session = await owner.ConfirmAsync(signIn, Presentation.BrowserProof, stop.Token);
            if (generation != scope) { await owner.LogoutAsync(session); return; }
            try { await Checkpoint(session, owner); }
            catch (CheckpointException) { await Cancel(); BlockStorage(); return; }
            if (generation != scope) { await owner.LogoutAsync(session); if (!memoryOnly) await Task.Run(() => store!.Forget()); return; }
            attempt = null; Activate(session, owner);
        }
        catch (Exception) { if (generation == scope) { Presentation.State = "confirmAccount"; Presentation.Status = "accountConfirmationFailed"; Changed?.Invoke(); } }
    }
    private sealed class CheckpointException : Exception { }
    private async Task Checkpoint(AccountDeviceSession session, AccountClient owner)
    {
        AccountSavedSession record = owner.Checkpoint(session);
        try { if (!memoryOnly) await Task.Run(() => store!.Save(record)); }
        catch (Exception)
        {
            try { await owner.LogoutAsync(session); } catch (Exception) { }
            try { await Task.Run(() => store!.Forget()); } catch (Exception) { }
            saved = null; device = null; throw new CheckpointException();
        }
        saved = record;
    }
    private void Activate(AccountDeviceSession session, AccountClient owner)
    {
        device = session; Presentation.BrowserProof = ""; Presentation.State = "signedIn"; Presentation.Status = "accountSignedIn";
        Presentation.Name = session.Account.Name; Presentation.Email = session.Account.Email; Presentation.StorageBlocked = false;
        Presentation.StorageNotice = memoryOnly ? memoryNotice : "accountSavedOnDevice"; Changed?.Invoke();
        refreshStop.Dispose(); refreshStop = new(); refresh = Renew(owner, refreshStop.Token);
    }
    private async Task ForgetStorage()
    {
        if (store is null) return;
        storageWork = true; Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        try { await Task.Run(store.Forget); saved = null; memoryOnly = false; Reset(); }
        catch (Exception) { BlockStorage(); }
        finally { storageWork = false; }
    }
    private async Task Renew(AccountClient owner, CancellationToken cancellation)
    {
        TimeSpan delay = TimeSpan.FromMinutes(8); long current = generation;
        while (!cancellation.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(delay, cancellation); if (saved is null) return;
                AccountDeviceSession result = await owner.ResumeAsync(saved, cancellation);
                if (generation != current) return;
                await Checkpoint(result, owner);
                if (generation != current) return;
                device = result; delay = TimeSpan.FromMinutes(8); Presentation.Status = "accountSignedIn"; Changed?.Invoke();
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return; }
            catch (CheckpointException) { owner.Dispose(); client = null; BlockStorage(); return; }
            catch (RoomProtocolException error) when (error.Message == "accountSignInRequired")
            {
                device = null; saved = null;
                owner.Dispose(); client = null;
                try { if (!memoryOnly) await Task.Run(() => store!.Forget()); Reset(); Presentation.Status = "accountSignInRequired"; Changed?.Invoke(); }
                catch (Exception) { BlockStorage(); } return;
            }
            catch (Exception) { if (cancellation.IsCancellationRequested) return; Presentation.Status = "accountUnavailable"; Changed?.Invoke(); delay = TimeSpan.FromSeconds(15); }
        }
    }
    private async Task SignOut()
    {
        if (client is not AccountClient owner) return;
        Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        long current = generation; refreshStop.Cancel(); await refresh;
        if (device is not AccountDeviceSession session) { BlockStorage(); return; }
        Presentation.Status = "accountWorking"; Changed?.Invoke();
        try { await owner.LogoutAsync(session); device = null; if (!memoryOnly) await Task.Run(() => store!.Forget()); saved = null; owner.Dispose(); client = null; Reset(); }
        catch (Exception) { if (device is null) { owner.Dispose(); client = null; BlockStorage(); } else { if (generation == current) Activate(session, owner); Presentation.Status = "accountSignOutFailed"; Changed?.Invoke(); } }
    }
    private async Task CancelAfter(Task previous) { await previous; await Cancel(); }
    private async Task Cancel()
    {
        if (attempt is AccountSignIn signIn && client is AccountClient owner) { try { await owner.CancelAsync(signIn); } catch (Exception) { } }
        if (attempt is not null && saved is not null && !memoryOnly) { try { await Task.Run(() => store!.Forget()); } catch (Exception) { } }
        attempt = null; client?.Dispose(); client = null; Reset();
    }
    public async Task DismissAsync()
    {
        if (attempt is null && (Presentation.State != "working" || device is not null)) return;
        bool wasStorage = restoring || storageWork; generation++; stop.Cancel(); await work;
        bool blocked = Presentation.StorageBlocked; await Cancel(); if (wasStorage || blocked) BlockStorage();
    }
    public Task CloseAsync() => closing.IsCompleted ? closing = CloseCore() : closing;
    private async Task CloseCore()
    {
        generation++; stop.Cancel(); refreshStop.Cancel(); await work; await refresh;
        if (memoryOnly && device is AccountDeviceSession session && client is AccountClient owner) { try { await owner.LogoutAsync(session); } catch (Exception) { } }
        device = null; await Cancel(); saved = null; store?.Dispose(); stop.Dispose(); refreshStop.Dispose(); stop = new(); refreshStop = new();
    }
    private void Reset() { string service = Presentation.Service; Presentation = new() { Service = service, StorageNotice = memoryOnly ? memoryNotice : "accountWillRemember" }; Changed?.Invoke(); }
    private void BlockStorage() { device = null; Presentation.State = "failed"; Presentation.StorageBlocked = true; Presentation.Status = "accountStorageUnavailable"; Presentation.StorageNotice = "accountSavedUnavailable"; Presentation.Name = ""; Presentation.Email = ""; Presentation.BrowserProof = ""; Changed?.Invoke(); }
    private void Fail() { Presentation.State = "failed"; Presentation.Status = "accountFailed"; Changed?.Invoke(); }
}
