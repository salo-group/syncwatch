namespace SyncWatch.RoomMetadata.Calls;

internal interface IMeetProvider
{
    Task<MeetSpace> Create(string account, CancellationToken cancel);
    Task<MeetSpace> Read(string account, string name, CancellationToken cancel);
    Task End(string account, string name, CancellationToken cancel);
}
