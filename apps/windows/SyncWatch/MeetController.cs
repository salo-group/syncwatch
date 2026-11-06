using System.Diagnostics;
using SyncWatch.Design;
using SyncWatch.Room;

namespace SyncWatch;

/// <summary>Room-owned permission and explicit browser handoff, independent of watch/player ownership.</summary>
internal sealed class MeetController(RoomController room, Func<Uri, CancellationToken, Task<bool>>? browser = null)
{
    private readonly Func<Uri, CancellationToken, Task<bool>> openBrowser = browser ?? OpenBrowser;
    private MeetPresentation model = new();
    private RoomCallMembership? membership;
    private RoomCallStatus? call, endTarget;
    private RoomMeetAuthorization? permission;
    private RoomMeetAuthorizationStatus? permissionStatus;
    private RoomCallOperation? operation;
    private Task work = Task.CompletedTask, cleanup = Task.CompletedTask;
    private CancellationTokenSource? stop;
    private long generation;
    private bool visible, closing, busy, browserOpened;
    private string? notice;
    public event Action? Changed;
    public MeetPresentation Presentation => closing ? new() { EnabledActions = [] }
        : membership is not null && room.IsCurrent(membership) ? model : new();
    internal LibraryPresentation LibraryPresentation(LibraryPresentation source)
    { return closing ? source : source with { EnabledActions = new HashSet<string>(source.EnabledActions) { "openCall" } }; }
    internal void MembershipChanged()
    {
        if (closing) return;
        var next = room.CallMembership();
        if (next is not null && membership is not null && ReferenceEquals(next.Owner, membership.Owner) && next.MemberId == membership.MemberId && next.Epoch == membership.Epoch) return;
        if (next is null && membership is null) return;
        Invalidate(next); if (visible && next is not null) Launch((_, _, _) => Task.CompletedTask);
    }
    internal void Present()
    { if (closing) return; visible = true; MembershipChanged(); if (membership is not null && work.IsCompleted) Launch((_, _, _) => Task.CompletedTask); }
    internal async Task DismissAsync() { visible = false; Invalidate(membership); await cleanup; }
    internal async Task CloseAsync() { closing = true; visible = false; Invalidate(null); await cleanup; }
    private void Invalidate(RoomCallMembership? next)
    {
        generation++; var previous = work; var previousStop = stop; var priorCleanup = cleanup; var flow = permission; var old = membership;
        previousStop?.Cancel(); stop = null; work = Task.CompletedTask; membership = next;
        call = null; endTarget = null; permission = null; permissionStatus = null; operation = null;
        notice = null; busy = false; browserOpened = false; model = new();
        cleanup = Clean(); Changed?.Invoke();
        async Task Clean()
        {
            await priorCleanup; await previous; previousStop?.Dispose();
            if (flow is not null && old is not null) { try { _ = await old.Owner.CancelMeetAuthorizationAsync(flow); } catch (Exception) { } }
        }
    }
    internal void Action(string action, string? value = null)
    {
        if (!visible || !Presentation.EnabledActions.Contains(action) || membership is not { } current || !room.IsCurrent(current)) return;
        if (action == "editMeetBrowserProof") { if (value is { Length: <= 32 }) { model.BrowserProof = value; Render(); } return; }
        if (action == "meetEnd") { endTarget = call; Render(); return; }
        if (action == "meetCancelEnd") { endTarget = null; Render(); return; }
        Launch(async (member, token, cancellation) =>
        {
            switch (action)
            {
                case "meetAuthorize":
                    var flow = await member.Owner.BeginMeetAuthorizationAsync(cancellation);
                    if (!Valid(token, member, cancellation)) { try { _ = await member.Owner.CancelMeetAuthorizationAsync(flow); } catch (Exception) { } return; }
                    permission = flow; permissionStatus = null; browserOpened = false; model.BrowserProof = ""; break;
                case "meetOpenAuthorization":
                    if (permission is null) return;
                    bool opened = await openBrowser(permission.BrowserUri, cancellation);
                    if (!Valid(token, member, cancellation)) return;
                    browserOpened = opened; if (!opened) notice = "meetBrowserUnavailable"; break;
                case "meetVerify":
                case "meetConfirm":
                    if (permission is null) return;
                    string code = model.BrowserProof.Trim().ToUpperInvariant();
                    var result = action == "meetVerify" ? await member.Owner.VerifyMeetAuthorizationAsync(permission, code, cancellation)
                        : await member.Owner.ConfirmMeetAuthorizationAsync(permission, code, cancellation);
                    if (!Valid(token, member, cancellation)) return; Apply(result); break;
                case "meetCancelAuthorization":
                    if (permission is not null) _ = await member.Owner.CancelMeetAuthorizationAsync(permission, cancellation);
                    if (!Valid(token, member, cancellation)) return;
                    permission = null; permissionStatus = null; browserOpened = false; model.BrowserProof = ""; break;
                case "meetStart":
                    operation = member.Owner.CallStartOperation();
                    var created = await member.Owner.RequestCallAsync(operation, cancellation);
                    if (!Valid(token, member, cancellation)) return; Apply(created); break;
                case "meetConfirmEnd":
                    if (endTarget is null || call?.CallId != endTarget.CallId || call?.Generation != endTarget.Generation) return;
                    operation = member.Owner.CallEndOperation(endTarget); endTarget = null;
                    var ended = await member.Owner.RequestCallAsync(operation, cancellation);
                    if (!Valid(token, member, cancellation)) return; Apply(ended); break;
                case "meetOpen":
                    if (call is not { State: "ready", MeetUri: not null } observed) return;
                    var fresh = await member.Owner.CallStatusAsync(cancellation);
                    if (!Valid(token, member, cancellation)) return;
                    Apply(fresh);
                    if (fresh.State != "ready" || fresh.CallId != observed.CallId || fresh.Generation != observed.Generation || fresh.MeetUri != observed.MeetUri) return;
                    bool accepted = await openBrowser(new Uri(observed.MeetUri), cancellation);
                    if (!Valid(token, member, cancellation) || call?.CallId != observed.CallId || call?.Generation != observed.Generation) return;
                    notice = accepted ? "meetHandedOff" : "meetBrowserUnavailable"; break;
                case "meetRetry": call = null; break; // Recover by reads; never automatically repeat a mutation.
                default: return;
            }
        });
    }
    private void Launch(Func<RoomCallMembership, long, CancellationToken, Task> body)
    {
        if (membership is not { } member || !room.IsCurrent(member) || closing) return;
        var previous = work; var oldStop = stop; var priorCleanup = cleanup; long token = ++generation;
        oldStop?.Cancel(); stop = new(); busy = true; notice = null; Render();
        work = Run(stop.Token);
        async Task Run(CancellationToken cancellation)
        {
            await priorCleanup; await previous; oldStop?.Dispose(); if (!Valid(token, member, cancellation)) return;
            try
            {
                await body(member, token, cancellation);
                if (!Valid(token, member, cancellation)) return; busy = false; Render();
                while (Valid(token, member, cancellation))
                {
                    long delay;
                    if (permission is { } flow)
                    {
                        var result = await member.Owner.MeetAuthorizationStatusAsync(flow, cancellation);
                        if (!Valid(token, member, cancellation)) return; Apply(result); delay = flow.PollAfterMs;
                    }
                    else
                    {
                        var result = await member.Owner.CallStatusAsync(cancellation);
                        if (!Valid(token, member, cancellation)) return; Apply(result); delay = result.PollAfterMs;
                    }
                    Render(); await Task.Delay(TimeSpan.FromMilliseconds(delay), cancellation);
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
            catch (Exception error)
            {
                if (Valid(token, member, cancellation)) { busy = false; notice = error is RoomProtocolException { Message: "malformed" } && permission is not null ? "meetInvalidConfirmation" : "meetFailed"; Render(); }
            }
        }
    }
    private bool Valid(long token, RoomCallMembership member, CancellationToken cancellation) => !cancellation.IsCancellationRequested && !closing && visible && generation == token && room.IsCurrent(member);
    private void Apply(RoomMeetAuthorizationStatus value)
    {
        permissionStatus = value;
        if (value.State == "expired") notice = "meetPermissionExpired";
        if (value.Reason is not null) notice = value.Reason == "accountMismatch" ? "meetAccountMismatch" : value.Reason == "invalidConfirmation" ? "meetInvalidConfirmation" : "meetPermissionUnavailable";
        if (value.State is "complete" or "cancelled" or "failed" or "expired")
        { permission = null; permissionStatus = null; model.BrowserProof = ""; browserOpened = false; if (value.State == "complete") call = null; }
    }
    private void Apply(RoomCallStatus value)
    { if (call?.CallId != value.CallId || call?.Generation != value.Generation) { endTarget = null; notice = null; } call = value; }
    private void Render()
    {
        if (membership is not { } member || !room.IsCurrent(member) || closing) { model = new(); Changed?.Invoke(); return; }
        var next = new MeetPresentation { BrowserProof = model.BrowserProof, EnabledActions = ["close"], AuthorizationState = permission is null ? "none" : permissionStatus?.State ?? "pending" };
        if (browserOpened && next.AuthorizationState == "pending") next.AuthorizationState = "authorizing";
        if (next.AuthorizationState == "confirmAuthorization" && permissionStatus?.Account is { } account) { next.Name = account.Name; next.Email = account.Email; }
        next.Status = busy ? "meetWorking" : notice ?? (permission is not null ? next.AuthorizationState switch { "browserConfirmationRequired" => "meetBrowserCode", "confirmAuthorization" => "meetPermissionConfirmation", _ => "meetAuthorizing" }
            : call?.State switch { "idle" => "meetIdle", "ready" => "meetReady", "creating" or "ending" => "meetWorking", "creationUnknown" or "endUnknown" => "meetUnknown", "ended" => "meetEnded", "unavailable" => "meetUnavailable", null => "meetWorking", _ => "meetFailed" });
        if (!busy)
        {
            if (permission is not null)
            {
                next.EnabledActions.Add("meetCancelAuthorization");
                if (next.AuthorizationState == "pending") next.EnabledActions.Add("meetOpenAuthorization");
                if (next.AuthorizationState == "browserConfirmationRequired") { next.EnabledActions.Add("editMeetBrowserProof"); if (next.BrowserProof.Length > 0) next.EnabledActions.Add("meetVerify"); }
                if (next.AuthorizationState == "confirmAuthorization") next.EnabledActions.Add("meetConfirm");
            }
            else if (call is not null)
            {
                next.EnabledActions.Add("meetRetry");
                if (member.IsOwner && call.AuthorizationRequired) next.EnabledActions.Add("meetAuthorize");
                if (call.State == "ready") next.EnabledActions.Add("meetOpen");
                if (member.IsOwner && !call.AuthorizationRequired)
                {
                    if (call.State is "idle" or "ended" or "creationFailed") next.EnabledActions.Add("meetStart");
                    if (call.State is "ready" or "endFailed") next.EnabledActions.Add("meetEnd");
                }
                if (endTarget is not null && endTarget.CallId == call.CallId && endTarget.Generation == call.Generation)
                { next.Status = "meetEndConfirmation"; next.EnabledActions = ["close", "meetConfirmEnd", "meetCancelEnd"]; }
            }
            else next.EnabledActions.Add("meetRetry");
        }
        model = next; Changed?.Invoke();
    }
    private static Task<bool> OpenBrowser(Uri uri, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        try { using var process = Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); return Task.FromResult(true); }
        catch (Exception) { return Task.FromResult(false); }
    }
}
