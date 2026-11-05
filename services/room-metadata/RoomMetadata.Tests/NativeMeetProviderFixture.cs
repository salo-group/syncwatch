using System.Security.Cryptography;
using SyncWatch.RoomMetadata.Calls;

// Test assembly only: native clients exercise ordinary room endpoints without contacting Google.
internal sealed class NativeMeetProviderFixture : IMeetProvider, IDisposable
{
    private sealed record Owned(string Account, MeetSpace Space);
    private readonly object gate = new();
    private readonly Dictionary<string, Owned> spaces = new(StringComparer.Ordinal);
    public Task<MeetSpace> Create(string account, CancellationToken cancel)
    {
        cancel.ThrowIfCancellationRequested();
        lock (gate)
        {
            string name = "spaces/fixture-" + Guid.NewGuid().ToString("N");
            string code = new(Enumerable.Range(0, 10).Select(_ => (char)('a' + RandomNumberGenerator.GetInt32(26))).ToArray());
            var space = new MeetSpace(name, "https://meet.google.com/" + code[..3] + "-" + code[3..7] + "-" + code[7..], "conferenceRecords/fixture-" + Guid.NewGuid().ToString("N"));
            spaces.Add(name, new(account, space)); return Task.FromResult(space);
        }
    }
    public Task<MeetSpace> Read(string account, string name, CancellationToken cancel)
    {
        cancel.ThrowIfCancellationRequested();
        lock (gate) { return Task.FromResult(Find(account, name).Space); }
    }
    public Task End(string account, string name, CancellationToken cancel)
    {
        cancel.ThrowIfCancellationRequested();
        lock (gate)
        {
            var value = Find(account, name); spaces[name] = value with { Space = value.Space with { Conference = null } };
            return Task.CompletedTask;
        }
    }
    private Owned Find(string account, string name) => spaces.TryGetValue(name, out var value) && value.Account == account
        ? value : throw new MeetProviderException("permissionRequired");
    public void Dispose() { lock (gate) spaces.Clear(); }
}
