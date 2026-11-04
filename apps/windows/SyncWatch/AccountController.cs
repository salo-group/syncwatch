using System.Diagnostics;
using SyncWatch.Room;

namespace SyncWatch;

/// <summary>Window-owned account work, isolated from movie and room lifetimes.</summary>
internal sealed class AccountController
{
    public Design.AccountPresentation Presentation { get; private set; } = new();
    public event Action? Changed;
    private AccountClient? client;
    private AccountSignIn? attempt;
    private AccountDeviceSession? device;
    private CancellationTokenSource stop = new();
    private CancellationTokenSource refreshStop = new();
    private Task work = Task.CompletedTask, refresh = Task.CompletedTask;
    private Task closing = Task.CompletedTask;
    private long generation;
    public Design.LibraryPresentation LibraryPresentation(Design.LibraryPresentation source)
    {
        return source with { EnabledActions = new HashSet<string>(source.EnabledActions) { "openAccount" } };
    }
    public void Action(string action, string? value)
    {
        if (action == "editAccountService" && Presentation.State is "signedOut" or "failed" && value is { Length: <= 256 }) { Presentation.Service = value; Changed?.Invoke(); }
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
        if (Presentation.State is not ("signedOut" or "failed") || attempt is not null) return;
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
            device = session; attempt = null; Presentation.BrowserProof = ""; Presentation.State = "signedIn"; Presentation.Status = "accountSignedIn";
            Presentation.Name = session.Account.Name; Presentation.Email = session.Account.Email; Changed?.Invoke();
            refreshStop.Dispose(); refreshStop = new(); refresh = Renew(owner, refreshStop.Token);
        }
        catch (Exception) { if (generation == scope) { Presentation.State = "confirmAccount"; Presentation.Status = "accountConfirmationFailed"; Changed?.Invoke(); } }
    }
    private async Task Renew(AccountClient owner, CancellationToken cancellation)
    {
        try
        {
            while (true) { await Task.Delay(TimeSpan.FromMinutes(8), cancellation); if (device is null) return; device = await owner.RefreshAsync(device, cancellation); }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception) { Presentation.Status = "accountFailed"; Changed?.Invoke(); }
    }
    private async Task SignOut()
    {
        if (client is not AccountClient owner || device is not AccountDeviceSession session) return;
        refreshStop.Cancel(); await refresh;
        Presentation.State = "working"; Presentation.Status = "accountWorking"; Changed?.Invoke();
        try { await owner.LogoutAsync(session); device = null; owner.Dispose(); client = null; Reset(); }
        catch (Exception) { Presentation.State = "signedIn"; Presentation.Status = "accountSignOutFailed"; Changed?.Invoke(); }
    }
    private async Task CancelAfter(Task previous) { await previous; await Cancel(); }
    private async Task Cancel()
    {
        if (attempt is AccountSignIn signIn && client is AccountClient owner) { try { await owner.CancelAsync(signIn); } catch (Exception) { } }
        attempt = null; client?.Dispose(); client = null; Reset();
    }
    public Task DismissAsync() => attempt is not null || Presentation.State == "working" && device is null ? CloseAsync() : Task.CompletedTask;
    public Task CloseAsync() => closing.IsCompleted ? closing = CloseCore() : closing;
    private async Task CloseCore()
    {
        generation++; stop.Cancel(); refreshStop.Cancel(); await work; await refresh;
        if (device is AccountDeviceSession session && client is AccountClient owner) { try { await owner.LogoutAsync(session); } catch (Exception) { } }
        device = null; await Cancel(); stop.Dispose(); refreshStop.Dispose(); stop = new(); refreshStop = new();
    }
    private void Reset() { string service = Presentation.Service; Presentation = new() { Service = service }; Changed?.Invoke(); }
    private void Fail() { Presentation.State = "failed"; Presentation.Status = "accountFailed"; Changed?.Invoke(); }
}
