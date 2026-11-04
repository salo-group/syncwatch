using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;

internal static class AccountChecks
{
    internal static void Run()
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-account-" + Guid.NewGuid().ToString("N"));
        var protection = new EphemeralDataProtectionProvider();
        var clock = new Clock();
        string proof = Secret(), subject = "provider-subject-a";
        DeviceSession session;
        try
        {
            using (var repository = new AccountRepository(directory, protection, clock))
            {
                var transactions = new SignInTransactions(repository, new Uri("https://accounts.example.invalid"), clock);
                SignInStart start = transactions.Start(Hash(proof));
                Require(start.BrowserUri == "https://accounts.example.invalid/auth/google/start?id=" + start.TransactionId);
                Reject(() => transactions.Poll(start.TransactionId, Secret()), "unauthorized");
                Require(transactions.Poll(start.TransactionId, proof).Status == "pending");
                transactions.BeginBrowser(start.TransactionId);
                Reject(() => transactions.BeginBrowser(start.TransactionId), "signInUnavailable");
                transactions.CompleteBrowser(start.TransactionId, subject, "Account A", "a@example.invalid");
                SignInStatus proposal = transactions.Poll(start.TransactionId, proof);
                Require(proposal.Status == "confirmAccount" && proposal.Account!.Email == "a@example.invalid" && proposal.Session is null);
                string confirmation = Secret();
                session = transactions.Confirm(start.TransactionId, proof, confirmation).Session!;
                Require(transactions.Confirm(start.TransactionId, proof, confirmation).Session == session);
                Reject(() => transactions.Confirm(start.TransactionId, proof, Secret()), "signInUnavailable");
                Require(repository.Authenticate(session.AccessToken).Id == session.Account.Id);
                DeviceSession sameSubject = repository.SignIn(subject, "New name", "new@example.invalid");
                Require(sameSubject.Account.Id == session.Account.Id);
                DeviceSession sameEmail = repository.SignIn("other-subject", "Other", "new@example.invalid");
                Require(sameEmail.Account.Id != session.Account.Id);
                string refreshId = Secret();
                DeviceSession rotated = repository.Refresh(session.RefreshToken, refreshId);
                Require(repository.Refresh(session.RefreshToken, refreshId) == rotated);
                Reject(() => repository.Authenticate(session.AccessToken), "unauthorized");
                Require(repository.Authenticate(rotated.AccessToken).Id == session.Account.Id);
                Reject(() => repository.Refresh(session.RefreshToken, Secret()), "sessionRevoked");
                Reject(() => repository.Authenticate(rotated.AccessToken), "unauthorized");
                Require(repository.Authenticate(sameSubject.AccessToken).Id == session.Account.Id);
                DeviceSession oldFamily = repository.SignIn("history-subject", "History", "history@example.invalid");
                DeviceSession intermediate = repository.Refresh(oldFamily.RefreshToken, Secret());
                DeviceSession newest = repository.Refresh(intermediate.RefreshToken, Secret());
                Reject(() => repository.Refresh(oldFamily.RefreshToken, Secret()), "sessionRevoked");
                Reject(() => repository.Authenticate(newest.AccessToken), "unauthorized");
                session = sameSubject;
                string cancelled = transactions.Start(Hash(proof)).TransactionId;
                transactions.BeginBrowser(cancelled); transactions.Cancel(cancelled, proof);
                Reject(() => transactions.CompleteBrowser(cancelled, subject, "Account", "a@example.invalid"), "signInUnavailable");
                string expired = transactions.Start(Hash(proof)).TransactionId;
                clock.Now = clock.Now.AddMinutes(6);
                Reject(() => transactions.Poll(expired, proof), "signInExpired");
                try { using var second = new AccountRepository(directory, protection); throw new InvalidOperationException("Writer lock bypassed"); }
                catch (IOException) { }
                byte[] protectedBytes = File.ReadAllBytes(Path.Combine(directory, "accounts.protected"));
                Require(!Encoding.UTF8.GetString(protectedBytes).Contains("example.invalid", StringComparison.Ordinal));
            }
            using (var restored = new AccountRepository(directory, protection, clock))
            {
                Require(restored.Authenticate(session.AccessToken).Id == session.Account.Id);
                clock.Now = clock.Now.AddMinutes(5);
                Reject(() => restored.Authenticate(session.AccessToken), "unauthorized");
                DeviceSession renewed = restored.Refresh(session.RefreshToken, Secret());
                restored.Logout(renewed.RefreshToken);
                Reject(() => restored.Authenticate(renewed.AccessToken), "unauthorized");
                Reject(() => restored.Refresh(renewed.RefreshToken, Secret()), "unauthorized");
            }
            File.WriteAllBytes(Path.Combine(directory, "accounts.protected"), [1, 2, 3]);
            try { using var broken = new AccountRepository(directory, protection); throw new InvalidOperationException("Corrupt store accepted"); }
            catch (CryptographicException) { }
            Console.WriteLine("Account checks passed: proof, confirmation/replay, stable subject, cancellation/expiry, encrypted restart, writer lock, rotation/revocation and corrupt-store rejection");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true); }
    }
    private static string Secret() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    private static string Hash(string value) => Convert.ToBase64String(SHA256.HashData(Encoding.ASCII.GetBytes(value))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Account assertion failed"); }
    private static void Reject(Action action, string code)
    {
        try { action(); } catch (AccountException error) when (error.Code == code) { return; }
        throw new InvalidOperationException("Account rejection missing: " + code);
    }
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
}
