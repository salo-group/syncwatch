using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Calls;

/// <summary>Provider credentials stay server-side, encrypted separately from device sessions.</summary>
public sealed class MeetGrantRepository : IDisposable
{
    internal sealed record Grant(string AccountId, string Subject, string RefreshToken, string Version, string Scope)
    {
        public override string ToString() => "[Google Meet grant redacted]";
    }
    private sealed record State(int Version, Grant[] Grants);
    private readonly object gate = new();
    private readonly AccountRepository accounts;
    private readonly ProtectedMeetStore<State> store;

    public MeetGrantRepository(AccountRepository accounts, string directory, IDataProtectionProvider protection)
    {
        this.accounts = accounts;
        store = new(directory, "grants", protection, () => new(1, []), Valid);
    }
    private static bool Valid(State state) => state.Version == 1 && state.Grants is { Length: <= MeetPolicy.MaximumGrants }
        && state.Grants.All(grant => grant is not null && AccountWire.Token(grant.AccountId) && AccountWire.Token(grant.Version)
            && AccountWire.Text(grant.Subject, 255) && MeetPolicy.Credential(grant.RefreshToken) && grant.Scope == MeetPolicy.CreatedScope)
        && state.Grants.Select(grant => grant.AccountId).Distinct(StringComparer.Ordinal).Count() == state.Grants.Length;

    // Invoke only after browser counterproof and explicit native confirmation of the matching Google account.
    public void Remember(AccountPrincipal principal, string subject, string? refreshToken)
    {
        _ = accounts.Profile(principal);
        AccountWire.Need(accounts.MatchesGoogleIdentity(principal, subject), "providerIdentityInvalid", 403);
        lock (gate)
        {
            Grant? previous = store.Current.Grants.SingleOrDefault(grant => grant.AccountId == principal.AccountId && grant.Subject == subject);
            string? credential = refreshToken ?? previous?.RefreshToken;
            AccountWire.Need(MeetPolicy.Credential(credential), "providerIdentityInvalid", 502);
            Grant replacement = new(principal.AccountId, subject, credential!, AccountWire.Secret(), MeetPolicy.CreatedScope);
            Grant[] next = [.. store.Current.Grants.Where(grant => grant.AccountId != principal.AccountId), replacement];
            AccountWire.Need(next.Length <= MeetPolicy.MaximumGrants, "accountCapacity", 503);
            store.Save(new(1, next));
        }
    }
    public bool HasAuthorization(AccountPrincipal principal)
    {
        _ = accounts.Profile(principal);
        return ReadForProvider(principal.AccountId) is not null;
    }
    public bool Forget(AccountPrincipal principal)
    {
        _ = accounts.Profile(principal);
        lock (gate)
        {
            Grant[] next = store.Current.Grants.Where(grant => grant.AccountId != principal.AccountId).ToArray();
            if (next.Length == store.Current.Grants.Length) return false;
            store.Save(new(1, next)); return true;
        }
    }
    internal Grant? ReadForProvider(string accountId)
    {
        Grant? result;
        lock (gate) { result = store.Current.Grants.SingleOrDefault(grant => grant.AccountId == accountId); }
        return result is not null && accounts.OwnsGoogleIdentity(result.AccountId, result.Subject) ? result : null;
    }
    // A delayed refresh cannot replace a newer authorization or restore a forgotten grant.
    internal Grant? ReplaceRefresh(Grant expected, string refreshToken)
    {
        AccountWire.Need(MeetPolicy.Credential(refreshToken), "providerIdentityInvalid", 502);
        lock (gate)
        {
            if (!store.Current.Grants.Any(grant => grant.AccountId == expected.AccountId && grant.Version == expected.Version)) return null;
            Grant next = expected with { RefreshToken = refreshToken, Version = AccountWire.Secret() };
            store.Save(new(1, store.Current.Grants.Select(grant => grant.AccountId == next.AccountId ? next : grant).ToArray()));
            return next;
        }
    }
    internal bool ForgetCurrent(Grant expected)
    {
        lock (gate)
        {
            if (!store.Current.Grants.Any(grant => grant.AccountId == expected.AccountId && grant.Version == expected.Version)) return false;
            store.Save(new(1, store.Current.Grants.Where(grant => grant.AccountId != expected.AccountId).ToArray()));
            return true;
        }
    }
    internal bool IsCurrent(Grant expected)
    {
        lock (gate) { return store.Current.Grants.Any(grant => grant.AccountId == expected.AccountId && grant.Version == expected.Version); }
    }
    public void Dispose() => store.Dispose();
}
