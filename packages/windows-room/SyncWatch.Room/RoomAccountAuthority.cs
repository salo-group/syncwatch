namespace SyncWatch.Room;

internal sealed record AccountRoomCredential(Uri Origin, Guid Owner, string AccountId, string Access)
{
    public override string ToString() => "[room account credential redacted]";
    internal static AccountRoomCredential From(AccountDeviceSession session) =>
        session.Issuer is not null && session.CredentialOwner is Guid owner
            ? new(session.Issuer, owner, session.Account.Id, session.Access) : throw new RoomProtocolException("malformed");
}

/// <summary>Borrowed account authority. Pause for rotation, commit after secure checkpoint; never persist access.</summary>
public sealed class RoomAccountAuthority : IDisposable
{
    public RoomAddress Address { get; }
    public string AccountId { get; }
    internal Guid Scope { get; } = Guid.NewGuid();
    private readonly Guid owner;
    private readonly object gate = new();
    private AccountRoomCredential? current;
    private TaskCompletionSource<AccountRoomCredential>? pending;
    private bool paused, closed;
    private int waiters;
    internal int PendingRequests { get { lock (gate) return waiters; } }
    internal RoomAccountAuthority(RoomAddress address, AccountDeviceSession session, Guid owner)
    {
        AccountRoomCredential credential = AccountRoomCredential.From(session);
        if (credential.Origin != address.Origin || credential.Owner != owner) throw new RoomProtocolException("malformed");
        Address = address; AccountId = credential.AccountId; this.owner = owner; current = credential;
    }
    public void Pause()
    {
        lock (gate) { NeedOpen(); current = null; paused = true; }
    }
    public void Commit(AccountDeviceSession session)
    {
        AccountRoomCredential credential = AccountRoomCredential.From(session);
        lock (gate)
        {
            NeedOpen();
            if (credential.Origin != Address.Origin || credential.Owner != owner || credential.AccountId != AccountId)
                throw new RoomProtocolException("malformed");
            current = credential; paused = false;
            pending?.TrySetResult(credential); pending = null;
        }
    }
    public void Unavailable()
    {
        lock (gate) { if (closed) return; current = null; paused = false; FailPending("transport"); }
    }
    public void Dispose()
    {
        lock (gate) { closed = true; current = null; paused = false; FailPending("closed"); }
    }
    private void FailPending(string code)
    {
        if (pending is null) return;
        pending.TrySetException(new RoomProtocolException(code));
        _ = pending.Task.Exception; // Observe a failure even when all independent callers already cancelled.
        pending = null;
    }
    internal async Task<AccountRoomCredential> CredentialAsync(CancellationToken cancellation = default)
    {
        cancellation.ThrowIfCancellationRequested();
        Task<AccountRoomCredential> task;
        lock (gate)
        {
            NeedOpen();
            if (current is not null) return current;
            if (!paused) throw new RoomProtocolException("transport");
            if (waiters >= 64) throw new RoomProtocolException("queueFull");
            waiters++;
            pending ??= new(TaskCreationOptions.RunContinuationsAsynchronously);
            task = pending.Task;
        }
        try
        {
            AccountRoomCredential credential = await task.WaitAsync(cancellation);
            cancellation.ThrowIfCancellationRequested();
            return credential;
        }
        finally { lock (gate) waiters--; }
    }
    private void NeedOpen() { if (closed) throw new RoomProtocolException("closed"); }
}
