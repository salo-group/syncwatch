using SyncWatch.Domain;
using SyncWatch.Room;

namespace SyncWatch;

internal sealed record RoomWatchContext(RoomBootstrap Bootstrap, RoomSelectionDefinition Definition);

/// <summary>One application-owned membership serializes bootstrap walks and command numbering.</summary>
internal sealed class RoomMembershipSession(RoomClient client, RoomAdmission admission, RoomIdentity identity)
{
    internal Task<RoomInvitation> InvitationAsync(CancellationToken cancellation = default) => client.InviteAsync(admission, cancellation);
    internal Task<IReadOnlyList<RoomAdmissionRequest>> PendingAdmissionsAsync(CancellationToken cancellation = default) => client.PendingAsync(admission, cancellation);
    internal Task DecideAdmissionAsync(string id, bool approve, CancellationToken cancellation = default) => client.DecideAsync(admission, id, approve, cancellation);
    internal Task<RoomSyncTicket> SyncTicketAsync(CancellationToken cancellation = default) => client.SyncTicketAsync(admission, cancellation);
    internal async Task<RoomWatchContext> AdmissionContextAsync(RoomBootstrap expected, CancellationToken cancellation = default)
    {
        ValidateBinding(expected);
        if (expected.Snapshot.WatchStatus != "active" || expected.Snapshot.MediaId is null) throw new RoomProtocolException("malformed");
        // Selection reauthorizes this generation without replacing a catalog cursor or taking its gate.
        for (int attempt = 0; attempt < 2; attempt++)
        {
            var definition = await client.SelectionAsync(admission, expected, cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (definition.Sequence >= minimumSequence) return new(expected, definition);
        }
        throw new RoomProtocolException("malformed");
    }
    // Calls are independent of ordered catalog/watch work.
    internal Task<RoomCallStatus> CallStatusAsync(CancellationToken cancellation = default) => client.CallStatusAsync(admission, cancellation);
    internal RoomCallOperation CallStartOperation() => client.CallStartOperation(admission);
    internal RoomCallOperation CallEndOperation(RoomCallStatus status) => client.CallEndOperation(admission, status);
    internal Task<RoomCallStatus> RequestCallAsync(RoomCallOperation operation, CancellationToken cancellation = default) => client.RequestCallAsync(operation, admission, cancellation);
    internal Task<RoomMeetAuthorization> BeginMeetAuthorizationAsync(CancellationToken cancellation = default) => client.BeginMeetAuthorizationAsync(admission, cancellation);
    internal Task<RoomMeetAuthorizationStatus> MeetAuthorizationStatusAsync(RoomMeetAuthorization flow, CancellationToken cancellation = default) => client.MeetAuthorizationStatusAsync(flow, admission, cancellation);
    internal Task<RoomMeetAuthorizationStatus> VerifyMeetAuthorizationAsync(RoomMeetAuthorization flow, string code, CancellationToken cancellation = default) => client.VerifyMeetAuthorizationAsync(flow, admission, code, cancellation);
    internal Task<RoomMeetAuthorizationStatus> ConfirmMeetAuthorizationAsync(RoomMeetAuthorization flow, string code, CancellationToken cancellation = default) => client.ConfirmMeetAuthorizationAsync(flow, admission, code, cancellation);
    internal Task<RoomMeetAuthorizationStatus> CancelMeetAuthorizationAsync(RoomMeetAuthorization flow, CancellationToken cancellation = default) => client.CancelMeetAuthorizationAsync(flow, admission, cancellation);
    private readonly SemaphoreSlim gate = new(1, 1);
    private long minimumSequence = admission.Bootstrap.Snapshot.Sequence;
    private long number;
    private RoomCommand? unresolved;

    internal async Task<RoomCatalogView> RefreshAsync(RoomBootstrap? initial = null, long sequence = 0, CancellationToken cancellation = default)
    {
        await gate.WaitAsync(cancellation);
        try { minimumSequence = Math.Max(minimumSequence, sequence); return await ReadAsync(initial, cancellation); }
        finally { gate.Release(); }
    }
    // Creating a socket bootstrap replaces the server cursor; serialize it with commands and walks.
    internal async Task<(RoomEventStream Stream, RoomCatalogView View)> ConnectEventsAsync(RoomClient events, bool recovering, CancellationToken cancellation)
    {
        await gate.WaitAsync(cancellation);
        try
        {
            if (recovering) _ = await ValidatedSnapshotAsync(null, cancellation);
            var stream = await events.OpenEventsAsync(admission, cancellation);
            _ = RoomBootstrap.InitialEvent(await stream.ReceiveAsync(cancellation), admission);
            return (stream, await ReadAsync(null, cancellation));
        }
        finally { gate.Release(); }
    }
    internal async Task<RoomCatalogView> PublishAsync(IReadOnlyList<MediaDescriptor> items, CancellationToken cancellation = default)
    {
        await gate.WaitAsync(cancellation);
        try
        {
            if (items.Count > 2048 || items.Select(p => p.MediaId).Distinct(StringComparer.Ordinal).Count() != items.Count) throw new RoomProtocolException("malformed");
            if (unresolved is not null) await AcknowledgeAsync(cancellation);
            for (int attempt = 0; attempt < 2; attempt++)
            {
                var snapshot = await ValidatedSnapshotAsync(null, cancellation);
                string batch = "batch-" + Guid.NewGuid().ToString("N"); int offset = 0;
                try
                {
                    do
                    {
                        var command = CatalogCommand(admission, number + 1, snapshot.Snapshot.WatchGeneration, batch, items, offset);
                        number++; unresolved = command.Command;
                        await AcknowledgeAsync(cancellation); offset += command.Count;
                    } while (offset < items.Count);
                    return await ReadAsync(null, cancellation);
                }
                catch (RoomProtocolException error) when (error.Message == "staleGeneration" && attempt == 0) { }
            }
            throw new RoomProtocolException("staleGeneration");
        }
        finally { gate.Release(); }
    }
    internal async Task<RoomWatchContext> WatchContextAsync(CancellationToken cancellation = default)
    {
        await gate.WaitAsync(cancellation);
        try
        {
            if (unresolved is not null) await AcknowledgeAsync(cancellation);
            return await ReadWatchContextAsync(cancellation);
        }
        finally { gate.Release(); }
    }
    // A peer generation change requires an explicit new user decision.
    internal async Task<RoomWatchContext> PerformAsync(RoomCommandAction action, RoomWatchContext expected, CancellationToken cancellation = default)
    {
        await gate.WaitAsync(cancellation);
        try
        {
            if (unresolved is not null) await AcknowledgeAsync(cancellation);
            var current = await ValidatedSnapshotAsync(null, cancellation);
            if (current.Snapshot.Epoch != expected.Bootstrap.Snapshot.Epoch || current.Snapshot.WatchGeneration != expected.Bootstrap.Snapshot.WatchGeneration)
                throw new RoomProtocolException("staleGeneration");
            var command = new RoomCommand(admission, number + 1, current.Snapshot.WatchGeneration, action);
            number++; unresolved = command;
            await AcknowledgeAsync(cancellation);
            return await ReadWatchContextAsync(cancellation);
        }
        finally { gate.Release(); }
    }
    private async Task<RoomWatchContext> ReadWatchContextAsync(CancellationToken cancellation)
    {
        for (int attempt = 0; attempt < 2; attempt++)
        {
            var bootstrap = await ValidatedSnapshotAsync(null, cancellation);
            try
            {
                var definition = await client.SelectionAsync(admission, bootstrap, cancellation);
                cancellation.ThrowIfCancellationRequested(); minimumSequence = Math.Max(minimumSequence, definition.Sequence);
                return new(bootstrap, definition);
            }
            catch (RoomProtocolException error) when (error.Message == "staleGeneration" && attempt == 0) { }
        }
        throw new RoomProtocolException("staleGeneration");
    }
    internal static (RoomCommand Command, int Count) CatalogCommand(RoomAdmission admission, long number, long generation, string batch, IReadOnlyList<MediaDescriptor> items, int offset)
    {
        if (offset < 0 || offset > items.Count) throw new RoomProtocolException("malformed");
        int count = Math.Min(100, items.Count - offset);
        while (true)
        {
            try { return (new(admission, number, generation, new RoomCommandAction.Catalog(batch, offset, items.Skip(offset).Take(count).ToArray(), offset + count == items.Count)), count); }
            catch (RoomProtocolException error) when (error.Message == "frameTooLarge" && count > 1) { count /= 2; }
        }
    }
    private async Task AcknowledgeAsync(CancellationToken cancellation)
    {
        if (unresolved is null) return;
        var result = await client.ExecuteAsync(admission, unresolved, cancellation);
        unresolved = null; minimumSequence = Math.Max(minimumSequence, result.Sequence);
        if (result.ErrorCode is string error) throw new RoomProtocolException(error);
        cancellation.ThrowIfCancellationRequested();
    }
    private async Task<RoomBootstrap> ValidatedSnapshotAsync(RoomBootstrap? initial, CancellationToken cancellation)
    {
        var value = initial ?? await client.SnapshotAsync(admission, cancellation);
        ValidateBinding(value);
        if (value.Snapshot.Sequence < minimumSequence) throw new RoomProtocolException("malformed");
        return value;
    }
    private void ValidateBinding(RoomBootstrap value)
    {
        if (value.Snapshot.Epoch != admission.Bootstrap.Snapshot.Epoch || value.Connection != admission.Bootstrap.Connection
            || !value.Snapshot.Members.Any(p => p.MemberId == admission.MemberId && p.Identity == identity)) throw new RoomProtocolException("malformed");
    }
    private async Task<RoomCatalogView> ReadAsync(RoomBootstrap? initial, CancellationToken cancellation)
    {
        var bootstrap = await ValidatedSnapshotAsync(initial, cancellation);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation); deadline.CancelAfter(TimeSpan.FromSeconds(20));
        var view = await client.CatalogViewAsync(admission, bootstrap, deadline.Token);
        cancellation.ThrowIfCancellationRequested(); minimumSequence = view.Bootstrap.Snapshot.Sequence;
        return view;
    }
}
