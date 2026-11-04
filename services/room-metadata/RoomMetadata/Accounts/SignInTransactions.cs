namespace SyncWatch.RoomMetadata.Accounts;

public sealed record SignInStatus(string Status, AccountProfile? Account = null, DeviceSession? Session = null);
public sealed record SignInStart(string TransactionId, string BrowserUri, int ExpiresIn = 300, int PollAfterMs = 2000);

/// <summary>Browser identity is a proposal. Only its proof-owning desktop can confirm or consume it.</summary>
public sealed class SignInTransactions(AccountRepository repository, Uri origin, TimeProvider? time = null)
{
    private sealed class Transaction(string challenge, DateTimeOffset until)
    {
        internal string Challenge { get; } = challenge;
        internal DateTimeOffset Until { get; set; } = until;
        internal string Status { get; set; } = "pending";
        internal string? Subject { get; set; }
        internal AccountProfile? Profile { get; set; }
        internal string? ConfirmationId { get; set; }
        internal DeviceSession? Reply { get; set; }
    }
    private readonly object gate = new();
    private readonly Dictionary<string, Transaction> transactions = [];
    private readonly TimeProvider clock = time ?? TimeProvider.System;

    public SignInStart Start(string challenge)
    {
        AccountWire.Need(AccountWire.Token(challenge));
        lock (gate)
        {
            foreach (string key in transactions.Where(pair => pair.Value.Until <= clock.GetUtcNow()).Select(pair => pair.Key).ToArray()) transactions.Remove(key);
            AccountWire.Need(transactions.Count < 128, "signInCapacity", 429);
            string id = AccountWire.Secret();
            transactions.Add(id, new(challenge, clock.GetUtcNow().AddMinutes(5)));
            return new(id, new Uri(origin, "/auth/google/start?id=" + id).AbsoluteUri);
        }
    }
    public void BeginBrowser(string id)
    {
        lock (gate)
        {
            Transaction transaction = Find(id);
            AccountWire.Need(transaction.Status == "pending", "signInUnavailable", 409);
            transaction.Status = "authorizing";
        }
    }
    public void CompleteBrowser(string id, string subject, string name, string email)
    {
        AccountWire.Need(AccountWire.Text(subject, 255) && AccountWire.Text(name, 128) && AccountWire.Text(email, 320), "providerIdentityInvalid", 502);
        lock (gate)
        {
            Transaction transaction = Find(id);
            AccountWire.Need(transaction.Status == "authorizing", "signInUnavailable", 409);
            transaction.Subject = subject;
            // No durable identity is created until the native confirmation.
            transaction.Profile = new("", name, email);
            transaction.Status = "confirmAccount";
        }
    }
    public void FailBrowser(string id)
    {
        lock (gate)
        {
            if (transactions.TryGetValue(id, out Transaction? transaction) && transaction.Status == "authorizing") transaction.Status = "failed";
        }
    }
    public SignInStatus Poll(string id, string proof)
    {
        lock (gate)
        {
            Transaction transaction = Prove(id, proof);
            return new(transaction.Status, transaction.Status == "confirmAccount" ? transaction.Profile : null);
        }
    }
    public SignInStatus Confirm(string id, string proof, string confirmationId)
    {
        AccountWire.Need(AccountWire.Token(confirmationId));
        lock (gate)
        {
            Transaction transaction = Prove(id, proof);
            if (transaction.Status == "complete" && transaction.ConfirmationId == confirmationId) return new("complete", Session: transaction.Reply);
            AccountWire.Need(transaction.Status == "confirmAccount" && transaction.Subject is not null && transaction.Profile is not null, "signInUnavailable", 409);
            DeviceSession reply = repository.SignIn(transaction.Subject!, transaction.Profile!.Name, transaction.Profile.Email);
            transaction.Reply = reply; transaction.ConfirmationId = confirmationId; transaction.Status = "complete";
            transaction.Until = clock.GetUtcNow().AddSeconds(30);
            return new("complete", Session: reply);
        }
    }
    public void Cancel(string id, string proof)
    {
        lock (gate)
        {
            Transaction transaction = Prove(id, proof);
            if (transaction.Reply is not null) repository.Logout(transaction.Reply.RefreshToken);
            transaction.Status = "cancelled"; transaction.Subject = null; transaction.Profile = null; transaction.Reply = null;
        }
    }
    private Transaction Find(string id)
    {
        AccountWire.Need(AccountWire.Token(id) && transactions.TryGetValue(id, out _), "signInExpired", 410);
        Transaction transaction = transactions[id];
        AccountWire.Need(transaction.Until > clock.GetUtcNow(), "signInExpired", 410);
        return transaction;
    }
    private Transaction Prove(string id, string proof)
    {
        Transaction transaction = Find(id);
        AccountWire.Need(AccountWire.Matches(proof, transaction.Challenge), "unauthorized", 401);
        return transaction;
    }
    public void Sweep()
    {
        lock (gate)
        {
            foreach (string key in transactions.Where(pair => pair.Value.Until <= clock.GetUtcNow()).Select(pair => pair.Key).ToArray()) transactions.Remove(key);
        }
    }
}
