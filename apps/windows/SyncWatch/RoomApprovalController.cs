using SyncWatch.Design;

namespace SyncWatch;

internal sealed record RoomApprovalPresentation(IReadOnlyList<RoomAdmissionRow> Rows, bool Available, bool Deciding, string Notice);

// Approval polling is independent of watch ordering and is owned/joined with its membership.
internal sealed class RoomApprovalController(RoomMembershipSession membership, Action<RoomApprovalPresentation> changed)
{
    private readonly CancellationTokenSource stop = new();
    private IReadOnlyList<RoomAdmissionRow> rows = [];
    private bool available, deciding, closed;
    private string notice = "";
    private Task? poll, decision, closing;
    private long revision;
    private void Publish() { if (!closed) changed(new(rows, available, deciding, notice)); }
    internal void Start() { if (poll is not null || closed) return; Publish(); poll = PollAsync(); }
    private async Task PollAsync()
    {
        try { while (!stop.IsCancellationRequested) { await ReadAsync(); await Task.Delay(TimeSpan.FromSeconds(3), stop.Token); } }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
    }
    private async Task ReadAsync()
    {
        long expected = revision;
        try
        {
            var values = (await membership.PendingAdmissionsAsync(stop.Token)).Select(p => new RoomAdmissionRow(p.Id, p.Account.Name, p.Account.Email)).ToArray();
            if (closed || stop.IsCancellationRequested || revision != expected) return;
            bool modified = !rows.SequenceEqual(values) || !available || notice.Length != 0;
            rows = Array.AsReadOnly(values); available = true; notice = "";
            if (modified) Publish();
        }
        catch (Exception)
        {
            if (closed || stop.IsCancellationRequested || revision != expected) return;
            available = false; notice = "roomPendingUnavailable"; Publish();
        }
    }
    internal void Decide(string id, bool approve)
    {
        if (closed || decision is not null || !available || !rows.Any(p => p.Id == id)) return;
        revision++; deciding = true; Publish(); decision = DecideAsync(id, approve);
    }
    private async Task DecideAsync(string id, bool approve)
    {
        await Task.Yield();
        try { await membership.DecideAdmissionAsync(id, approve, stop.Token); }
        catch (Exception) { if (!closed && !stop.IsCancellationRequested) notice = "roomAdmissionChanged"; }
        if (closed || stop.IsCancellationRequested) return;
        revision++; await ReadAsync();
        if (closed || stop.IsCancellationRequested) return;
        deciding = false; decision = null; Publish();
    }
    internal Task CloseAsync() => closing ??= CloseCoreAsync();
    private async Task CloseCoreAsync()
    {
        closed = true; await stop.CancelAsync();
        await Task.WhenAll(new[] { poll, decision }.Where(p => p is not null).Cast<Task>());
        stop.Dispose(); poll = null; decision = null;
    }
}
