using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Rooms;

namespace SyncWatch.RoomMetadata.Calls;

public sealed record MeetAuthorizationStart(string TransactionId, string BrowserUri, int ExpiresIn, int PollAfterMs);
public sealed record MeetAuthorizationStatus(string Status, AccountProfile? Account = null, string? Reason = null);

/// <summary>Room-owned browser permission proposals; only the proof-owning device may confirm a grant.</summary>
public sealed class MeetAuthorizationTransactions(AccountRepository accounts, AuthenticatedRooms rooms,
    MeetGrantRepository grants, Uri origin, TimeProvider? time = null)
{
    private sealed class Transaction(RoomMemberAccess scope, string challenge, DateTimeOffset until)
    {
        internal readonly RoomMemberAccess Scope = scope;
        internal readonly string Challenge = challenge;
        internal DateTimeOffset Until = until;
        internal string Status = "pending";
        internal string? Reason, Subject, RefreshToken, BrowserProofHash, ConfirmationId;
        internal AccountProfile? Profile;
        internal bool? ManagedDomain;
        internal int ProofFailures;
        internal void ClearProposal() { Subject = null; RefreshToken = null; Profile = null; ManagedDomain = null; }
    }
    private readonly object gate = new();
    private readonly Dictionary<string, Transaction> transactions = [];
    private readonly TimeProvider clock = time ?? TimeProvider.System;

    public MeetAuthorizationStart Start(RoomMemberAccess scope, string challenge)
    {
        AccountWire.Need(AccountWire.Token(challenge));
        NeedCurrent(scope);
        lock (gate)
        {
            SweepCore(); AccountWire.Need(transactions.Count < 128, "queueFull", 429);
            AccountWire.Need(transactions.Values.Count(value => Same(value.Scope.Principal, scope.Principal) && value.Status is "pending" or "authorizing" or "browserConfirmationRequired" or "confirmAuthorization") < 4,
                "queueFull", 429);
            string id = AccountWire.Secret(); transactions.Add(id, new(scope, challenge, clock.GetUtcNow() + MeetPolicy.AuthorizationLifetime));
            return new(id, new Uri(origin, "/auth/google/meet-start?id=" + id).AbsoluteUri,
                checked((int)MeetPolicy.AuthorizationLifetime.TotalSeconds), MeetPolicy.AuthorizationPollMilliseconds);
        }
    }
    public void BeginBrowser(string id)
    {
        lock (gate)
        {
            Transaction transaction = Find(id); NeedCurrent(transaction.Scope);
            AccountWire.Need(transaction.Status == "pending", "conflict", 409);
            transaction.Status = "authorizing";
        }
    }
    // Called only with the SDK handler's verified UserInfo identity and token response, never from a native request.
    public string? CompleteBrowser(string id, string subject, string? refreshToken, bool scopeGranted, bool? managedDomain = null)
    {
        lock (gate)
        {
            Transaction transaction = Find(id);
            AccountWire.Need(transaction.Status == "authorizing", "conflict", 409);
            if (!rooms.IsCurrent(transaction.Scope)) { Fail(transaction, "roomChanged"); return null; }
            if (!accounts.MatchesGoogleIdentity(transaction.Scope.Principal, subject)) { Fail(transaction, "accountMismatch"); return null; }
            if (!scopeGranted || refreshToken is not null && !MeetPolicy.Credential(refreshToken)
                || refreshToken is null && !grants.HasAuthorization(transaction.Scope.Principal))
            { Fail(transaction, "permissionUnavailable"); return null; }
            transaction.Subject = subject; transaction.RefreshToken = refreshToken;
            transaction.ManagedDomain = managedDomain;
            transaction.Profile = accounts.Profile(transaction.Scope.Principal);
            string proof = AccountWire.BrowserProof(); transaction.BrowserProofHash = AccountWire.Hash(proof);
            transaction.Status = "browserConfirmationRequired";
            return proof;
        }
    }
    public void FailBrowser(string id)
    {
        lock (gate)
        {
            if (transactions.TryGetValue(id, out var transaction) && transaction.Status == "authorizing") Fail(transaction, "permissionUnavailable");
        }
    }
    public MeetAuthorizationStatus Poll(AccountPrincipal principal, string id, string proof)
    {
        lock (gate) { return View(Prove(principal, id, proof)); }
    }
    public MeetAuthorizationStatus Verify(AccountPrincipal principal, string id, string proof, string browserProof)
    {
        lock (gate)
        {
            Transaction transaction = Prove(principal, id, proof);
            if (transaction.Status == "expired") return View(transaction);
            AccountWire.Need(transaction.Status is "browserConfirmationRequired" or "confirmAuthorization", "conflict", 409);
            if (!CheckBrowserProof(transaction, browserProof)) return View(transaction);
            transaction.Status = "confirmAuthorization"; transaction.Reason = null;
            return View(transaction);
        }
    }
    public MeetAuthorizationStatus Confirm(AccountPrincipal principal, string id, string proof, string requestId, string browserProof)
    {
        AccountWire.Need(AccountWire.Token(requestId));
        lock (gate)
        {
            Transaction transaction = Prove(principal, id, proof);
            if (transaction.Status == "expired") return View(transaction);
            if (transaction.Status == "complete")
            {
                AccountWire.Need(transaction.ConfirmationId == requestId && AccountWire.BrowserProofMatches(browserProof, transaction.BrowserProofHash), "conflict", 409);
                return View(transaction);
            }
            AccountWire.Need(transaction.Status == "confirmAuthorization" && transaction.Subject is not null, "conflict", 409);
            if (!CheckBrowserProof(transaction, browserProof)) return View(transaction);
            // Confirmation is the consent boundary. Concurrent room leave cannot authorize a meeting for that closed epoch.
            grants.Remember(principal, transaction.Subject!, transaction.RefreshToken, transaction.ManagedDomain);
            transaction.ClearProposal(); transaction.ConfirmationId = requestId; transaction.Status = "complete"; transaction.Reason = null;
            transaction.Until = clock.GetUtcNow().AddSeconds(30);
            return View(transaction);
        }
    }
    public MeetAuthorizationStatus Cancel(AccountPrincipal principal, string id, string proof)
    {
        lock (gate)
        {
            Transaction transaction = Prove(principal, id, proof, requireRoom: false);
            // A confirmed global permission remains valid; cancel never revokes a previously accepted grant.
            if (transaction.Status == "complete") return View(transaction);
            transaction.ClearProposal(); transaction.BrowserProofHash = null; transaction.Status = "cancelled"; transaction.Reason = null;
            return View(transaction);
        }
    }
    private static bool Same(AccountPrincipal first, AccountPrincipal second) => first.AccountId == second.AccountId && first.SessionId == second.SessionId;
    private Transaction Prove(AccountPrincipal principal, string id, string proof, bool requireRoom = true)
    {
        Transaction transaction = Find(id);
        AccountWire.Need(Same(transaction.Scope.Principal, principal) && AccountWire.Matches(proof, transaction.Challenge), "unauthorized", 401);
        AccountWire.Need(accounts.IsActive(principal), "unauthorized", 401);
        if (requireRoom && transaction.Status != "expired") NeedCurrent(transaction.Scope);
        return transaction;
    }
    private Transaction Find(string id)
    {
        AccountWire.Need(AccountWire.Token(id) && transactions.TryGetValue(id, out _), "unavailable", 404);
        Transaction transaction = transactions[id];
        Expire(transaction); return transaction;
    }
    private bool CheckBrowserProof(Transaction transaction, string browserProof)
    {
        if (AccountWire.BrowserProofMatches(browserProof, transaction.BrowserProofHash)) return true;
        transaction.Reason = "invalidConfirmation";
        if (++transaction.ProofFailures >= 3) Fail(transaction, "invalidConfirmation");
        return false;
    }
    private void NeedCurrent(RoomMemberAccess scope) => AccountWire.Need(scope.Owner && rooms.IsCurrent(scope), "forbidden", 403);
    private static MeetAuthorizationStatus View(Transaction transaction) => new(transaction.Status,
        transaction.Status == "confirmAuthorization" ? transaction.Profile : null, transaction.Reason);
    private static void Fail(Transaction transaction, string reason)
    {
        transaction.ClearProposal(); transaction.BrowserProofHash = null; transaction.Status = "failed"; transaction.Reason = reason;
    }
    private void Expire(Transaction transaction)
    {
        if (transaction.Until <= clock.GetUtcNow())
        { transaction.ClearProposal(); transaction.BrowserProofHash = null; transaction.Status = "expired"; transaction.Reason = null; }
    }
    private void SweepCore()
    {
        foreach (var transaction in transactions.Values)
        {
            Expire(transaction);
            if (transaction.Status is "pending" or "authorizing" or "browserConfirmationRequired" or "confirmAuthorization"
                && !rooms.IsCurrent(transaction.Scope)) Fail(transaction, "roomChanged");
        }
        foreach (string id in transactions.Where(pair => pair.Value.Until.AddSeconds(30) <= clock.GetUtcNow()).Select(pair => pair.Key).ToArray()) transactions.Remove(id);
    }
    public void Sweep() { lock (gate) SweepCore(); }
}
