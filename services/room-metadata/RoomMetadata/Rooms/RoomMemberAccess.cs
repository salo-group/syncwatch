using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Rooms;

/// <summary>Immutable membership facts for independent server aggregates; contains no legacy token.</summary>
public sealed record RoomMemberAccess(string RoomId, string RoomEpoch, string OwnerAccountId, AccountPrincipal Principal,
    string MemberId, string ClientRunId, bool Owner)
{
    public override string ToString() => "[room member access]";
}
