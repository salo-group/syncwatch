using System.Windows.Threading;
using SyncWatch.Design;
using SyncWatch.Domain;
using SyncWatch.Library;
using SyncWatch.Room;

namespace SyncWatch;

internal sealed record RoomWatchMembership(RoomMembershipSession Owner, string MemberId, RoomIdentity Identity, string Epoch);
internal sealed record RoomInvitationTransfer(RoomMembershipSession Owner, RoomInvitation Invitation, Guid Revision);
internal sealed record RoomPreparedPublication(Guid Lease, MediaDescriptor Media);

/// <summary>The workspace owns membership independently of the visible dialog.</summary>
internal sealed class RoomController
{
    private readonly Dispatcher dispatcher = Dispatcher.CurrentDispatcher;
    public RoomEditor Editor { get; }
    internal RoomAccountAuthority? AccountAuthority { get; private set; }
    private readonly Func<RoomAccountAuthority?>? accountSource;
    internal RoomController(bool legacyValidation = false, Func<RoomAccountAuthority?>? accountSource = null)
    { this.accountSource = accountSource; Editor = new(flow: legacyValidation ? "legacy" : SyncWatchRoomForm.DefaultFlow); }
    private bool CurrentAccount => !Editor.RequiresAccount || AccountAuthority is not null && (accountSource is null || ReferenceEquals(accountSource(), AccountAuthority));
    internal void BindAccount(RoomAccountAuthority? authority, string name)
    {
        if (!Editor.RequiresAccount) return;
        AccountAuthority = authority; Editor.AccountReady = authority is not null;
        Editor.AccountName = authority is null ? "" : name; Editor.AccountService = authority?.Address.Origin.AbsoluteUri ?? "";
        Changed?.Invoke();
    }
    public RoomCatalogView? CatalogView { get; private set; }
    public RoomBootstrap? Bootstrap => CatalogView?.Bootstrap;
    internal RoomBootstrap? WatchBootstrap { get; private set; }
    internal bool HasRoomScope => Editor.State != "disconnected" || work is not null || playbackSession is not null;
    public bool Connected => Editor.Connected && Bootstrap is not null && CurrentAccount;
    public event Action? Changed;
    private RoomApprovalController? approvals;
    private RoomAdmission? admission;
    private CancellationTokenSource? stop;
    private Task? work;
    private RoomMembershipSession? session, playbackSession;
    private string? playbackEpoch;
    private RoomLocalCatalog catalogMap = new();
    private Guid? localRevision, acknowledgedRevision;
    private IReadOnlyList<LocalMediaFile> localFiles = [];
    private Task? publisher;
    private Task? missingReport;
    private (Guid Lease, MediaDescriptor Media)? prepared;
    private RoomPreparedPublication? acknowledgedPrepared;
    private bool stopping;
    private Task? closing;
    public void Connect()
    {
        dispatcher.VerifyAccess();
        if (stopping || Editor.Busy || Connected || work is not null || !Editor.IsValid) return;
        if (!CurrentAccount) return;
        playbackSession = null; playbackEpoch = null;
        var values = Editor.Values; bool owner = Editor.Mode == "createRoom";
        Editor.State = "connecting"; Editor.Status = "";
        stop = new(); work = RunAsync(values, owner, stop.Token, AccountAuthority); Changed?.Invoke();
    }
    public LibraryPresentation LibraryPresentation(LibraryPresentation source, bool sharedStartAvailable = false)
    {
        dispatcher.VerifyAccess();
        var enabled = new HashSet<string>(source.EnabledActions); if (!stopping) enabled.Add("openRoom");
        // Membership alone cannot authorize an unsynchronized local Start in a room.
        if (Editor.State != "disconnected" && !(Connected && sharedStartAvailable)) enabled.Remove("startWatching");
        return source with { EnabledActions = enabled, RoomStatus = Connected ? (Editor.Status is "roomCatalogPublishing" or "roomCatalogPublishFailed" ? Editor.Status : "roomMetadataConnected") : Editor.State == "reconnecting" ? "reconnecting" : Editor.Busy ? "roomConnecting" : Editor.State == "failed" ? "roomConnectionFailed" : "disconnected" };
    }
    internal string MediaNotice
    {
        get
        {
            if (Editor.State == "reconnecting") return "reconnecting";
            if (Editor.State == "failed") return playbackSession is null ? "roomConnectionFailed" : "roomUpdatesUnavailable";
            if (!Connected || Bootstrap is not { } value || value.Snapshot.WatchStatus != "active" || admission is null) return "";
            if (value.MissingMembers.Contains(admission.MemberId) || localFiles.Count == 0 && prepared is null) return "sharedWatchMediaMissing";
            return value.MissingMembers.Count == 0 ? "" : "roomPeersMissingMedia";
        }
    }
    private void StartMissingReport()
    {
        if (stopping || !Connected || missingReport is not null || localFiles.Count != 0 || prepared is not null ||
            Bootstrap is not { } value || value.Snapshot.WatchStatus != "active" || value.Snapshot.MediaId is not string mediaId ||
            admission is null || value.MissingMembers.Contains(admission.MemberId) || stop is null) return;
        missingReport = ReportMissingAsync(value, mediaId, localRevision, stop.Token);
    }
    private async Task ReportMissingAsync(RoomBootstrap value, string mediaId, Guid? revision, CancellationToken token)
    {
        await Task.Yield();
        try
        {
            var intent = await BeginWatchAsync(token);
            token.ThrowIfCancellationRequested();
            if (stopping || localFiles.Count != 0 || prepared is not null || localRevision != revision ||
                intent.Context.Bootstrap.Snapshot.Epoch != value.Snapshot.Epoch ||
                intent.Context.Bootstrap.Snapshot.WatchGeneration != value.Snapshot.WatchGeneration ||
                intent.Context.Bootstrap.Snapshot.WatchStatus != "active" || intent.Context.Bootstrap.Snapshot.MediaId != mediaId) return;
            await PerformWatchAsync(new RoomCommandAction.Missing(mediaId, true), intent.Membership, intent.Context, token);
        }
        catch (Exception) { /* A later membership/catalog update retries under its current generation. */ }
        finally
        {
            missingReport = null;
            if (localRevision != revision || Bootstrap?.Snapshot.Epoch != value.Snapshot.Epoch ||
                Bootstrap?.Snapshot.WatchGeneration != value.Snapshot.WatchGeneration) StartMissingReport();
        }
    }
    public void PasteInvitation(string text)
    {
        dispatcher.VerifyAccess();
        if (stopping || Editor.Busy || Editor.Connected || Editor.Mode != "joinRoom") return;
        try
        {
            var invitation = RoomInvitation.Parse(text);
            Editor.Edit("roomID", invitation.RoomId); Editor.Edit("invitation", invitation.Credential.CopyInvitation());
        }
        catch (RoomProtocolException)
        {
            Editor.ClearSecrets(); Editor.Edit("roomID", ""); Editor.Status = "roomInvitationInvalid";
        }
        Changed?.Invoke();
    }
    internal void DecideAdmission(string id, bool approve)
    {
        if (!stopping && Connected && Editor.Owner) approvals?.Decide(id, approve);
    }
    private bool invitationCopyBusy;
    private Guid invitationCopyRevision;
    internal async Task<RoomInvitationTransfer?> InvitationForCopyAsync()
    {
        if (invitationCopyBusy || stopping || !Connected || !Editor.Owner || session is null || admission is null) return null;
        var owner = session; var value = admission;
        invitationCopyBusy = true; Guid revision = invitationCopyRevision = Guid.NewGuid();
        try
        {
            RoomInvitation invitation = AccountAuthority is not null ? await owner.InvitationAsync()
                : new(value.RoomId, value.Invitation ?? throw new RoomProtocolException("malformed"));
            var transfer = new RoomInvitationTransfer(owner, invitation, revision); return CanCopy(transfer) ? transfer : null;
        }
        catch (Exception) { if (Connected && ReferenceEquals(session, owner)) { Editor.Status = "roomInvitationCopyFailed"; Changed?.Invoke(); } return null; }
        finally { invitationCopyBusy = false; }
    }
    internal bool CanCopy(RoomInvitationTransfer transfer) => !stopping && Connected && Editor.Owner && ReferenceEquals(session, transfer.Owner) && invitationCopyRevision == transfer.Revision;
    internal void InvitationCopyCompleted(bool copied, RoomInvitationTransfer transfer) { if (CanCopy(transfer)) InvitationCopyCompleted(copied); }
    private void StartApprovals(RoomMembershipSession owner)
    {
        if (approvals is not null || !Connected || !Editor.Owner || AccountAuthority is null) return;
        approvals = new(owner, value =>
        {
            if (stopping || !Connected || !ReferenceEquals(session, owner)) return;
            Editor.PendingRequests = value.Rows; Editor.PendingAvailable = value.Available; Editor.Deciding = value.Deciding; Editor.PendingStatus = value.Notice;
            Changed?.Invoke();
        });
        approvals.Start();
    }
    public string? InvitationToCopy() => Connected && admission?.Invitation is { } invitation ? new RoomInvitation(admission.RoomId, invitation).CopyText() : null;
    public void InvitationCopyCompleted(bool copied)
    {
        dispatcher.VerifyAccess();
        if (!Connected) return;
        Editor.Status = copied ? "roomInvitationCopied" : "roomInvitationCopyFailed"; Changed?.Invoke();
    }
    public Task CloseAsync() { dispatcher.VerifyAccess(); return closing ??= StopAsync(); }
    private async Task StopAsync()
    {
        await Task.Yield();
        stopping = true; playbackSession = null; playbackEpoch = null; stop?.Cancel();
        if (work is not null) await work;
        work = null; stop?.Dispose(); stop = null; admission = null; CatalogView = null;
        Editor.State = "disconnected"; Editor.Status = ""; Editor.RoomId = ""; Editor.MemberCount = 0; Editor.Owner = false; Editor.ClearSecrets();
        stopping = false; closing = null; Changed?.Invoke();
    }
    public void UpdateLibrary(Guid revision, Func<IReadOnlyList<LocalMediaFile>> files)
    {
        dispatcher.VerifyAccess();
        if (localRevision == revision) return;
        localRevision = revision; localFiles = files().ToArray(); StartPublisher(); StartMissingReport();
    }
    internal bool IsCurrent(RoomWatchMembership membership)
    {
        dispatcher.VerifyAccess();
        return !stopping && Connected && ReferenceEquals(session, membership.Owner) && admission?.MemberId == membership.MemberId
            && admission?.Bootstrap.Snapshot.Epoch == membership.Epoch;
    }
    // Existing playback survives only transport interruption, never a replacement membership.
    internal bool CanContinuePlayback(RoomWatchMembership membership) => !stopping && CurrentAccount && ReferenceEquals(playbackSession, membership.Owner) && playbackEpoch == membership.Epoch;
    internal static bool IsMetadataInterruption(Exception error) => error switch
    {
        RoomProtocolException protocol => protocol.Message is "transport" or "closed",
        System.Net.Http.HttpRequestException => true,
        System.Net.WebSockets.WebSocketException => true,
        System.IO.IOException => true,
        OperationCanceledException => true,
        _ => false
    };
    internal async Task<RoomSyncTicket> SyncTicketAsync(RoomWatchMembership membership, CancellationToken cancellation = default)
    {
        if (AccountAuthority is null || !IsCurrent(membership)) throw new RoomProtocolException("closed");
        var ticket = await membership.Owner.SyncTicketAsync(cancellation);
        cancellation.ThrowIfCancellationRequested();
        if (AccountAuthority is null || !IsCurrent(membership)) throw new RoomProtocolException("closed");
        return ticket;
    }
    internal async Task<(RoomWatchMembership Membership, RoomWatchContext Context)> BeginWatchAsync(CancellationToken cancellation = default)
    {
        dispatcher.VerifyAccess();
        if (session is null || admission is null) throw new RoomProtocolException("closed");
        var member = admission.Bootstrap.Snapshot.Members.Single(p => p.MemberId == admission.MemberId);
        var membership = new RoomWatchMembership(session, admission.MemberId, member.Identity, admission.Bootstrap.Snapshot.Epoch);
        return (membership, await WatchContextAsync(membership, cancellation));
    }
    internal async Task<RoomWatchContext> WatchContextAsync(RoomWatchMembership membership, CancellationToken cancellation = default)
    {
        if (!IsCurrent(membership)) throw new RoomProtocolException("closed");
        var value = await membership.Owner.WatchContextAsync(cancellation);
        cancellation.ThrowIfCancellationRequested();
        if (!IsCurrent(membership)) throw new RoomProtocolException("closed");
        RecordWatch(value.Bootstrap);
        return value;
    }
    internal async Task<RoomWatchContext> PerformWatchAsync(RoomCommandAction action, RoomWatchMembership membership, RoomWatchContext expected, CancellationToken cancellation = default)
    {
        if (!IsCurrent(membership)) throw new RoomProtocolException("closed");
        var value = await membership.Owner.PerformAsync(action, expected, cancellation);
        cancellation.ThrowIfCancellationRequested();
        if (!IsCurrent(membership)) throw new RoomProtocolException("closed");
        RecordWatch(value.Bootstrap);
        return value;
    }
    internal async Task<RoomPreparedPublication> PublishPreparedAsync(MediaDescriptor media, RoomWatchMembership membership, CancellationToken cancellation = default)
    {
        if (!IsCurrent(membership) || media.FullHashSha256 is null || media.DurationMs is not > 0) throw new RoomProtocolException("malformed");
        Guid lease = Guid.NewGuid(); prepared = (lease, media); localRevision = Guid.NewGuid(); StartPublisher();
        try
        {
            while (true)
            {
                cancellation.ThrowIfCancellationRequested();
                if (!IsCurrent(membership) || prepared?.Lease != lease) throw new RoomProtocolException("closed");
                if (acknowledgedPrepared is { } accepted && accepted.Lease == lease) return accepted;
                if (publisher is null) throw new RoomProtocolException("unavailable");
                await publisher.WaitAsync(cancellation);
            }
        }
        catch { ClearPrepared(lease, membership); throw; }
    }
    internal void ClearPrepared(Guid lease, RoomWatchMembership membership)
    {
        if (!IsCurrent(membership) || prepared?.Lease != lease) return;
        prepared = null; localRevision = Guid.NewGuid(); StartPublisher();
    }
    private void StartPublisher()
    {
        if (stopping || !Connected || publisher is not null || session is null || localRevision is null || localRevision == acknowledgedRevision || stop is null) return;
        publisher = PublishLibraryAsync(session, stop.Token);
    }
    private async Task PublishLibraryAsync(RoomMembershipSession owner, CancellationToken cancellation)
    {
        await Task.Yield();
        try
        {
            while (!cancellation.IsCancellationRequested && !stopping && ReferenceEquals(session, owner) && localRevision is Guid revision && revision != acknowledgedRevision)
            {
                try
                {
                    var loaded = prepared;
                    var publication = catalogMap.Prepare(localFiles, loaded?.Media); Editor.Status = "roomCatalogPublishing"; Changed?.Invoke();
                    var view = await owner.PublishAsync(publication.Items, cancellation);
                    cancellation.ThrowIfCancellationRequested(); if (stopping || !ReferenceEquals(session, owner)) return;
                    catalogMap.Acknowledge(publication); acknowledgedRevision = revision; Accept(view);
                    acknowledgedPrepared = loaded is { } entry ? new(entry.Lease, publication.Items.Single(p => p.MediaId == publication.LocalIds[entry.Media.MediaId])) : null;
                    if (Editor.Status is "roomCatalogPublishing" or "roomCatalogPublishFailed") Editor.Status = "";
                    Changed?.Invoke();
                }
                catch (Exception)
                {
                    if (cancellation.IsCancellationRequested || stopping || !ReferenceEquals(session, owner)) return;
                    if (localRevision != revision) continue;
                    Editor.Status = "roomCatalogPublishFailed"; Changed?.Invoke(); return;
                }
            }
        }
        finally { publisher = null; }
    }
    private void Accept(RoomCatalogView view)
    {
        RecordWatch(view.Bootstrap);
        if (view.Bootstrap.Snapshot.Sequence < (Bootstrap?.Snapshot.Sequence ?? 0)) return;
        CatalogView = view; Editor.MemberCount = view.Bootstrap.Snapshot.Members.Count; StartMissingReport();
    }
    private void RecordWatch(RoomBootstrap bootstrap)
    {
        if (bootstrap.Snapshot.Sequence >= (WatchBootstrap?.Snapshot.Sequence ?? 0)) WatchBootstrap = bootstrap;
    }
    private async Task RunAsync(IReadOnlyDictionary<string, string> values, bool owner, CancellationToken cancellation, RoomAccountAuthority? authority)
    {
        await Task.Yield(); RoomClient? current = null; RoomAdmission? admitted = null; bool failed = false, interrupted = false;
        AccountRoomAdmission? pending = null; string failureNotice = authority is null ? "roomConnectionFailed" : "roomRequestUnavailable";
        try
        {
            current = authority is null ? new RoomClient(new(values["serviceAddress"])) : RoomClient.ForAccount(authority);
            RoomIdentity identity;
            if (authority is not null)
            {
                pending = new(current, owner, values);
                admitted = await pending.PerformAsync(() => { Editor.State = "awaitingApproval"; Changed?.Invoke(); }, cancellation);
                identity = admitted.Bootstrap.Snapshot.Members.First(p => p.MemberId == admitted.MemberId).Identity;
                pending = null;
            }
            else
            {
                identity = new RoomIdentity("run-" + Guid.NewGuid().ToString("N"), values["username"]);
                admitted = owner ? await current.CreateAsync(identity, new(values["syncplayHost"], int.Parse(values["syncplayPort"], System.Globalization.CultureInfo.InvariantCulture), values["roomName"]), new(values["creationKey"]), cancellation)
                    : await current.JoinAsync(values["roomID"], new(values["invitation"]), identity, cancellation);
            }
            cancellation.ThrowIfCancellationRequested();
            if (!CurrentAccount) throw new RoomProtocolException("closed");
            admission = admitted; Editor.ClearSecrets();
            var ownerSession = new RoomMembershipSession(current, admitted, identity);
            session = ownerSession; catalogMap = new(); acknowledgedRevision = null; prepared = null; acknowledgedPrepared = null;
            await RoomEventMonitor.RunAsync(current.Address, ownerSession,
                () => { Editor.State = "reconnecting"; Changed?.Invoke(); },
                view =>
                {
                    playbackSession = ownerSession; playbackEpoch = admitted.Bootstrap.Snapshot.Epoch;
                    Editor.State = "connected"; Editor.RoomId = admitted.RoomId; Editor.Owner = owner;
                    Accept(view); StartApprovals(ownerSession); StartPublisher(); Changed?.Invoke();
                }, cancellation, authority);
        }
        catch (Exception error)
        {
            if (error is RoomAdmissionException admissionError) failureNotice = admissionError.Notice;
            failed = !cancellation.IsCancellationRequested;
            interrupted = failed && IsMetadataInterruption(error) && playbackSession is not null;
            if (!interrupted) { playbackSession = null; playbackEpoch = null; }
            stop?.Cancel(); Editor.State = "connecting"; Changed?.Invoke();
        }
        finally
        {
            if (approvals is not null) await approvals.CloseAsync(); approvals = null;
            Editor.PendingRequests = []; Editor.PendingStatus = ""; Editor.Deciding = false; Editor.PendingAvailable = false;
            if (missingReport is not null) await missingReport;
            missingReport = null;
            if (publisher is not null) await publisher;
            publisher = null; session = null; acknowledgedRevision = null; prepared = null; acknowledgedPrepared = null; catalogMap = new();
            WatchBootstrap = null;
            if (current is not null)
            {
                if (!interrupted)
                {
                    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                    try { if (admitted is not null) await current.LeaveAsync(admitted, timeout.Token); else if (pending is not null) await pending.CancelAsync(timeout.Token); } catch (Exception) { }
                }
                await current.DisposeAsync();
            }
            admission = null; CatalogView = null; Editor.ClearSecrets(); Editor.MemberCount = 0; Editor.Owner = false; work = null; stop?.Dispose(); stop = null; if (failed) { Editor.State = "failed"; Editor.Status = failureNotice; }
            Changed?.Invoke();
        }
    }
}
