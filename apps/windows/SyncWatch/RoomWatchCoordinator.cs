using SyncWatch.Bridge;
using SyncWatch.Design;
using SyncWatch.Domain;
using SyncWatch.Room;

namespace SyncWatch;

internal sealed class SharedMediaUnavailableException : Exception { }

// Owns room admission and the helper; the watch owner retains native playback.
internal sealed class RoomWatchCoordinator(RoomController room, WatchSessionCoordinator watch, HelperConfiguration helper, LibraryController? library = null)
{
    private sealed record ActiveWatch(RoomWatchMembership Membership, RoomWatchContext Context, PlaybackWorkspace Player);
    private sealed record NextWatch(LibraryMediaSelection Selection, bool ForceSelection);
    private LibraryMediaSelection? pendingSelection;
    private ActiveWatch? active;
    private CancellationTokenSource? cancellation;
    private Task? work;
    private bool seenMembership, closing, admitted, completed;
    internal bool Busy { get; private set; }
    internal string Status { get; private set; } = "";
    internal string? ErrorKey { get; private set; }
    internal event Action? Changed;
    internal void ClearError() => ErrorKey = null;
    internal LibraryPresentation LibraryPresentation(LibraryPresentation source)
    {
        var result = room.LibraryPresentation(source, !Busy && active is null);
        var enabled = new HashSet<string>(result.EnabledActions);
        if (room.Connected && !Busy && active is null && watch.RuntimeAvailable && source.SelectedId is not null) enabled.Add("startWatching");
        if (Busy || closing) enabled.Remove("startWatching");
        return result with { EnabledActions = enabled, RoomStatus = !Busy && room.MediaNotice.Length > 0 ? room.MediaNotice : Status.Length > 0 ? Status : result.RoomStatus };
    }
    internal string PlayerNotice => Busy ? "" : room.MediaNotice.Length > 0 ? room.MediaNotice
        : active?.Player.SharedState?.Connection == HelperConnection.Reconnecting ? "reconnecting" : "";
    internal void MembershipChanged()
    {
        if (!room.Connected) { seenMembership = false; return; }
        if (seenMembership) return;
        seenMembership = true;
        if (work is null && watch.Player is { } player)
        {
            player.RequireSharedAdmission(); Busy = true;
            work = GateExistingAsync(player);
        }
    }
    private async Task GateExistingAsync(PlaybackWorkspace player)
    {
        await Task.Yield();
        try { await player.RevokeSharedAsync(); }
        finally { Busy = false; work = null; Changed?.Invoke(); }
    }
    internal void Start(LibraryMediaSelection selection)
    {
        if (closing || work is not null) return;
        if (room.Editor.State == "disconnected") { watch.Start(selection, autoplay: true); return; }
        if (!room.Connected || !watch.RuntimeAvailable) return;
        Busy = true; ErrorKey = null; Status = "sharedWatchPreparing";
        cancellation = new(); work = RunAsync(selection, cancellation.Token); Changed?.Invoke();
    }
    private async Task RunAsync(LibraryMediaSelection initial, CancellationToken token, bool forceSelection = false)
    {
        try
        {
            NextWatch? next = new(initial, forceSelection);
            while (!closing && !token.IsCancellationRequested && next is { } current)
            {
                next = await RunOneAsync(current.Selection, current.ForceSelection, token);
            }
        }
        finally { Busy = false; work = null; Changed?.Invoke(); }
    }
    private async Task<NextWatch?> RunOneAsync(LibraryMediaSelection selection, bool forceSelection, CancellationToken token)
    {
        await Task.Yield();
        RoomPreparedPublication? publication = null; RoomWatchMembership? membership = null; PlaybackWorkspace? owned = null; bool finished = false, follow = false, startedGeneration = false; admitted = false; completed = false;
        string stage = "membership";
        try
        {
            var intent = await room.BeginWatchAsync(token); membership = intent.Membership;
            stage = "runtime";
            var runtime = await helper.VerifyAsync(token);
            stage = "input";
            token.ThrowIfCancellationRequested(); watch.End(); watch.Start(selection, requiresSharedAdmission: true);
            var scope = watch.Scope; long deadline = Environment.TickCount64 + 20000;
            while (owned is null)
            {
                token.ThrowIfCancellationRequested();
                if (!room.IsCurrent(intent.Membership) || watch.Scope != scope || watch.ErrorKey is not null || Environment.TickCount64 >= deadline) throw new InvalidOperationException("media-unavailable");
                // Native readiness does not acknowledge restored position and initial track preferences.
                if (watch.Phase == WatchPhase.Active && watch.Player is { } player && player.Scope == scope)
                {
                    try
                    {
                        var observation = await player.Player.ObserveAsync(scope, token);
                        if (observation.Status == PlaybackStatus.Ready && observation.Seekable && observation.DurationMs is > 0) owned = player;
                    }
                    catch (Playback.PlaybackException) { }
                }
                if (owned is null) await Task.Delay(100, token);
            }
            stage = "media";
            var local = await owned.PrepareSharedMediaAsync(token);
            stage = "publication";
            publication = await room.PublishPreparedAsync(local, intent.Membership, token);
            stage = "generation";
            var context = await room.WatchContextAsync(intent.Membership, token);
            RequireSameGeneration(intent.Context, context);
            if (forceSelection || context.Bootstrap.Snapshot.WatchStatus != "active")
            {
                stage = "selection";
                context = await room.PerformWatchAsync(new RoomCommandAction.Select(publication.Media.MediaId), intent.Membership, context, token);
                if (context.Definition.Selection?.Media != publication.Media) throw new InvalidOperationException("selection-changed");
                stage = "start";
                context = await room.PerformWatchAsync(new RoomCommandAction.Start(publication.Media.MediaId), intent.Membership, context, token);
                startedGeneration = true;
            }
            await RequireAvailableAsync(local, intent.Membership, context, token);
            stage = "agreement";
            var current = await room.WatchContextAsync(intent.Membership, token);
            RequireSameGeneration(context, current); await RequireAvailableAsync(local, intent.Membership, current, token);
            if (!ReferenceEquals(watch.Player, owned) || watch.Scope != scope) throw new InvalidOperationException("media-changed");
            active = new(intent.Membership, current, owned);
            token.ThrowIfCancellationRequested();
            stage = "helper";
            var transport = HelperProcess.Start(runtime.Executable, runtime.Package);
            try
            {
                token.ThrowIfCancellationRequested();
                if (!Authorized()) throw new InvalidOperationException("watch-revoked");
                var connection = current.Bootstrap.Connection; var remote = current.Definition.Selection!.Media;
                await owned.ConnectSharedAsync(transport,
                    new(connection.Host, connection.Port, connection.Room, intent.Membership.Identity.SyncplayUsername),
                    new("loaded-" + Guid.NewGuid().ToString("N"), remote.Title, local.DurationMs!.Value, (ulong)local.FileSizeBytes),
                    runtime.Revision, requireDeclaredIdentity: true, authority: Authorized,
                    admissionProvider: room.AccountAuthority is null ? null : async cancellation =>
                    {
                        return await TransportAdmissionAsync(intent.Membership, current, local, cancellation);
                    });
            }
            catch { await transport.DisposeAsync(); throw; }
            long readyDeadline = Environment.TickCount64 + 20000;
            while (true)
            {
                token.ThrowIfCancellationRequested();
                if (pendingSelection is not null) break;
                if (admitted && room.Connected && room.WatchBootstrap is { } latest && latest.Snapshot.Epoch == current.Bootstrap.Snapshot.Epoch &&
                    latest.Snapshot.WatchGeneration > current.Bootstrap.Snapshot.WatchGeneration && latest.Snapshot.WatchStatus is "selected" or "active")
                { owned.RequireSharedAdmission(); follow = true; break; }
                var actual = await owned.Player.ObserveAsync(scope, token);
                if (completed || actual.Ended)
                {
                    if (!admitted) { finished = true; Status = "ended"; break; }
                    if (!Authorized()) throw new InvalidOperationException("watch-revoked");
                    if (!completed)
                    {
                        Busy = true; Changed?.Invoke();
                        await owned.RevokeSharedAsync();
                        if (publication is { } prepared)
                        { room.ClearPrepared(prepared.Lease, intent.Membership); publication = null; }
                        if (room.Editor.Owner && room.IsCurrent(intent.Membership))
                        {
                            try { await room.PerformWatchAsync(new RoomCommandAction.End(RoomWatchEndReason.Finished), intent.Membership, current, token); }
                            catch (OperationCanceledException) when (token.IsCancellationRequested) { throw; }
                            catch (Exception) { /* Completion metadata cannot control a peer's movie. */ }
                        }
                        token.ThrowIfCancellationRequested();
                        completed = true; Busy = false; Status = "ended"; Changed?.Invoke();
                    }
                    // Keep only generation observation; the completed helper is already joined.
                    await Task.Delay(100, token);
                    continue;
                }
                if (!Authorized() || owned.SharedState is { Connection: HelperConnection.Failed or HelperConnection.Stopped }) throw new InvalidOperationException("watch-revoked");
                if (owned.SharedState is { Connection: HelperConnection.Connected, PlaybackReady: true } state)
                {
                    if (state.AcceptedUsername != intent.Membership.Identity.SyncplayUsername) throw new InvalidOperationException("identity-changed");
                    if (!admitted)
                    {
                        if (current.Bootstrap.MissingMembers.Contains(intent.Membership.MemberId))
                        {
                            var recovered = await room.PerformWatchAsync(new RoomCommandAction.Missing(current.Definition.Selection!.Media.MediaId, false), intent.Membership, current, token);
                            RequireSameGeneration(current, recovered);
                        }
                        owned.AdmitSharedPlayback(autoplay: startedGeneration); admitted = true; stage = "playback";
                    }
                    if (Busy || Status == "reconnecting") { Busy = false; Status = "sharedWatchConnected"; Changed?.Invoke(); }
                }
                else if (owned.SharedState?.Connection == HelperConnection.Reconnecting)
                { if (Status != "reconnecting") { Status = "reconnecting"; Changed?.Invoke(); } }
                else if (!admitted && Environment.TickCount64 >= readyDeadline) throw new TimeoutException();
                await Task.Delay(100, token);
            }
        }
        // Native and helper adapters can report transport errors after cancellation.
        catch (Exception) when (closing || token.IsCancellationRequested) { }
        catch (RoomProtocolException error) when (error.Message == "staleGeneration") { follow = true; }
        catch (Exception error)
        {
            ErrorKey = FailureKey(error, owned?.SharedState?.IdentityRejected == true);
            // Fixed vocabulary only; never serialize exception text, paths or room credentials.
            Console.Error.WriteLine($"syncwatch.shared-watch failure stage={stage} key={ErrorKey}");
            Status = ErrorKey;
        }
        finally
        {
            var previous = active; bool completedAdmission = admitted; active = null; admitted = false; completed = false;
            try
            {
                if (owned is not null) await owned.RevokeSharedAsync();
                if (!completedAdmission && owned is not null && ReferenceEquals(watch.Player, owned)) watch.End();
                if (finished && previous is not null && room.Editor.Owner && room.IsCurrent(previous.Membership))
                {
                    try { await room.PerformWatchAsync(new RoomCommandAction.End(RoomWatchEndReason.Finished), previous.Membership, previous.Context); }
                    catch (Exception) { /* Completion metadata never changes another player's timing. */ }
                }
            }
            finally
            {
                if (publication is not null && membership is not null) room.ClearPrepared(publication.Lease, membership);
            }
        }
        if (closing || token.IsCancellationRequested) return null;
        if (pendingSelection is { } requested)
        {
            pendingSelection = null; Busy = true; Status = "sharedWatchPreparing"; Changed?.Invoke();
            return new(requested, true);
        }
        if (follow && membership is not null)
        {
            try
            {
                Busy = true; Status = "sharedWatchPreparing"; Changed?.Invoke();
                long deadline = Environment.TickCount64 + 20000;
                while (true)
                {
                    token.ThrowIfCancellationRequested();
                    var current = await room.WatchContextAsync(membership, token);
                    if (current.Bootstrap.Snapshot.WatchStatus == "active" && current.Definition.Selection is { } selected)
                    {
                        var candidate = ResolvePlaylist(selected.Media);
                        if (candidate is null)
                        {
                            await room.PerformWatchAsync(new RoomCommandAction.Missing(selected.Media.MediaId, true), membership, current, token);
                            throw new SharedMediaUnavailableException();
                        }
                        library?.Select(candidate.Media.Id); return new(candidate, false);
                    }
                    if (current.Bootstrap.Snapshot.WatchStatus != "selected" || Environment.TickCount64 >= deadline) throw new TimeoutException();
                    await Task.Delay(100, token);
                }
            }
            catch (Exception) when (closing || token.IsCancellationRequested) { return null; }
            catch (Exception error) { watch.End(); ErrorKey = FailureKey(error, false); Status = ErrorKey; Changed?.Invoke(); }
        }
        return null;
    }
    private bool Authorized()
    {
        if (closing || pendingSelection is not null || active is not { } value || !ReferenceEquals(watch.Player, value.Player) || watch.Scope != value.Player.Scope) return false;
        // Metadata generations authorize new loads, not play/pause/seek of an admitted file.
        if (admitted) return room.CanContinuePlayback(value.Membership);
        return room.IsCurrent(value.Membership) && room.WatchBootstrap is { } current
            && current.Snapshot.Epoch == value.Context.Bootstrap.Snapshot.Epoch
            && current.Snapshot.WatchGeneration == value.Context.Bootstrap.Snapshot.WatchGeneration && current.Snapshot.WatchStatus == "active";
    }
    private bool CanAdmitTransport() => Authorized() && active is { } current
        && (room.IsCurrent(current.Membership) || admitted && room.CanRenewWatchAdmission(current.Membership))
        && room.WatchBootstrap is { } latest && latest.Snapshot.Epoch == current.Context.Bootstrap.Snapshot.Epoch
        && latest.Snapshot.WatchGeneration == current.Context.Bootstrap.Snapshot.WatchGeneration
        && latest.Snapshot.WatchStatus == "active" && latest.Snapshot.MediaId == current.Context.Bootstrap.Snapshot.MediaId;
    private async Task<SyncplayAdmission> TransportAdmissionAsync(RoomWatchMembership membership, RoomWatchContext expected, MediaDescriptor local, CancellationToken token)
    {
        // The helper bounds retries. Only an established scope may outlive the event transport.
        while (true)
        {
            token.ThrowIfCancellationRequested();
            if (!CanAdmitTransport()) throw new InvalidOperationException("watch-revoked");
            try
            {
                var ticket = await room.SyncAdmissionAsync(membership, expected, fresh => { RequireSameGeneration(expected, fresh); RequireMatch(local, fresh); }, token);
                if (!CanAdmitTransport()) throw new InvalidOperationException("watch-revoked");
                return new(ticket.AdmissionCredential(), membership.Identity.ClientRunId);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { throw; }
            catch (Exception error)
            {
                if (!admitted || !Authorized() || room.Editor.State != "reconnecting" || !RoomController.IsMetadataInterruption(error)) throw;
                await Task.Delay(100, token);
            }
        }
    }
    internal static string FailureKey(Exception error, bool identityRejected) => identityRejected ? "sharedWatchIdentityRejected"
        : error is RoomProtocolException { Message: "staleGeneration" } ? "sharedWatchRoomChanged"
        : error is SharedMediaUnavailableException ? "sharedWatchMediaMissing" : "sharedWatchFailed";
    internal static void RequireSameGeneration(RoomWatchContext expected, RoomWatchContext current)
    {
        if (expected.Bootstrap.Snapshot.Epoch != current.Bootstrap.Snapshot.Epoch || expected.Bootstrap.Snapshot.WatchGeneration != current.Bootstrap.Snapshot.WatchGeneration) throw new RoomProtocolException("staleGeneration");
    }
    internal static void RequireMatch(MediaDescriptor local, RoomWatchContext context)
    {
        if (context.Bootstrap.Snapshot.WatchStatus != "active" || context.Definition.Selection is not { } selected) throw new InvalidOperationException("watch-inactive");
        if (MediaAgreement.Resolve(selected.Media, [local]).Status != MediaAgreementStatus.Matched) throw new SharedMediaUnavailableException();
    }
    private async Task RequireAvailableAsync(MediaDescriptor local, RoomWatchMembership membership, RoomWatchContext context, CancellationToken token)
    {
        try { RequireMatch(local, context); }
        catch (SharedMediaUnavailableException)
        {
            if (!context.Bootstrap.MissingMembers.Contains(membership.MemberId))
                await room.PerformWatchAsync(new RoomCommandAction.Missing(context.Definition.Selection!.Media.MediaId, true), membership, context, token);
            throw;
        }
    }
    private async Task StopAsync()
    {
        pendingSelection = null; cancellation?.Cancel(); var pending = work;
        if (pending is not null) await pending;
        cancellation?.Dispose(); cancellation = null;
    }
    private static (string Title, long Size) Key(Library.LocalMediaFile file) =>
        (MediaDescriptor.NormalizedTitleKey(MediaDescriptor.LogicalTitle(file.Title)), file.SizeBytes);
    private bool SharedPlaylistAvailable
    {
        get
        {
            if (!admitted || !room.Connected || active is not { } value || !ReferenceEquals(watch.Player, value.Player) || watch.Scope != value.Player.Scope) return false;
            if (!completed) return value.Player.SharedState?.Connection == HelperConnection.Connected;
            return room.IsCurrent(value.Membership) && room.WatchBootstrap is { } latest &&
                CompletedPlaylistAvailable(value.Context.Bootstrap.Snapshot, latest.Snapshot);
        }
    }
    internal static bool CompletedPlaylistAvailable(RoomSnapshot expected, RoomSnapshot latest)
    {
        if (expected.Epoch != latest.Epoch || expected.WatchStatus != "active" || expected.MediaId is null || expected.MediaId != latest.MediaId) return false;
        if (latest.WatchGeneration == expected.WatchGeneration) return latest.WatchStatus == "active";
        // watch.ended advances the same movie exactly once; later loads are separate authority.
        return latest.WatchStatus == "ended" && latest.WatchGeneration > expected.WatchGeneration &&
            latest.WatchGeneration - expected.WatchGeneration == 1;
    }
    internal void UpdatePlaylist(WatchPlaylist playlist)
    {
        if (library is null || closing) { playlist.Update([], null, false); return; }
        var files = library.RoomFiles.ToDictionary(file => file.Id);
        bool local = room.Editor.State == "disconnected";
        bool enabled = watch.Phase == WatchPhase.Active && !Busy && (local || SharedPlaylistAvailable);
        var view = room.CatalogView;
        var members = view?.Bootstrap.Snapshot.Members.Select(member => member.MemberId).ToArray() ?? [];
        var keys = view?.Items.GroupBy(item => item.MemberId).ToDictionary(group => group.Key,
            group => group.Select(item => (item.Media.TitleKey, item.Media.FileSizeBytes)).ToHashSet());
        var entries = library.Presentation.Entries.Where(row => files.ContainsKey(row.Id))
            .Select(row => new SelectionMenuEntry(row.Id, row.Title)).ToArray();
        var neighbors = files.Values.Where(file => !local && members.Length > 1 && members.All(member =>
            keys is not null && keys.TryGetValue(member, out var available) && available.Contains(Key(file))))
            .Select(file => file.Id).ToHashSet();
        playlist.Update(entries, watch.MediaId, enabled, neighbors);
    }
    internal void SelectPlaylist(string id)
    {
        if (closing || Busy || library is null || watch.MediaId == id || library.PlaylistMedia(id) is not { } selection) return;
        var check = new WatchPlaylist(); UpdatePlaylist(check); check.Open();
        if (check.Menu?.Allows(id) != true) return;
        if (room.Editor.State == "disconnected") { library.Select(id); watch.Start(selection, autoplay: true); }
        else if (SharedPlaylistAvailable)
        {
            pendingSelection = selection; Busy = true; Status = "sharedWatchPreparing";
            active?.Player.RequireSharedAdmission(); Changed?.Invoke();
        }
    }
    private LibraryMediaSelection? ResolvePlaylist(MediaDescriptor media)
    {
        if (library is null) return null;
        var candidates = library.RoomFiles.Where(file => Key(file) == (media.TitleKey, media.FileSizeBytes)).Take(2).ToArray();
        return candidates.Length == 1 ? library.PlaylistMedia(candidates[0].Id) : null;
    }
    internal async Task EndAsync()
    {
        var previous = active; await StopAsync();
        if (previous is not null && room.Editor.Owner && room.IsCurrent(previous.Membership))
        {
            try { await room.PerformWatchAsync(new RoomCommandAction.End(RoomWatchEndReason.User), previous.Membership, previous.Context); }
            catch (Exception) { /* Local End already released its own playback. */ }
        }
        watch.End(); Status = ""; Changed?.Invoke();
    }
    private long accountBinding;
    private Task accountBindingWork = Task.CompletedTask;
    internal Task BindAccountAsync(RoomAccountAuthority? authority, string name)
    {
        if (closing) return Task.CompletedTask;
        long revision = ++accountBinding;
        return accountBindingWork = BindAccountAfterAsync(accountBindingWork, authority, name, revision);
    }
    private async Task BindAccountAfterAsync(Task previous, RoomAccountAuthority? authority, string name, long revision)
    {
        await previous;
        if (closing || accountBinding != revision) return;
        if (!ReferenceEquals(room.AccountAuthority, authority) && room.HasRoomScope) await LeaveAsync();
        if (!closing && accountBinding == revision) room.BindAccount(authority, name);
    }
    internal async Task LeaveAsync()
    {
        await StopAsync(); await room.CloseAsync();
        if (watch.Player is { } player) await player.RevokeSharedAsync(releaseAdmission: true);
        Status = ""; seenMembership = false; Changed?.Invoke();
    }
    internal async Task CloseAsync() { accountBinding++; closing = true; await accountBindingWork; await StopAsync(); }
}
