using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;

internal static class AccountAuthorityChecks
{
    internal static void Run()
    {
        string directory = Path.Combine(Path.GetTempPath(), "syncwatch-authority-" + Guid.NewGuid().ToString("N"));
        var protection = new EphemeralDataProtectionProvider();
        var clock = new Clock();
        DeviceSession surviving;
        AccountPrincipal oldProcess;
        try
        {
            using (var repository = new AccountRepository(directory, protection, clock))
            {
                DeviceSession first = repository.SignIn("subject", "First", "first@example.invalid");
                DeviceSession second = repository.SignIn("subject", "Second", "first@example.invalid");
                DeviceSession other = repository.SignIn("other", "Other", "other@example.invalid");
                AccountPrincipal principal = repository.Authorize(first.AccessToken);
                AccountPrincipal sibling = repository.Authorize(second.AccessToken);
                AccountPrincipal foreign = repository.Authorize(other.AccessToken);
                Require(principal.AccountId == sibling.AccountId && principal.SessionId != sibling.SessionId);
                Require(principal.AccountId != foreign.AccountId && repository.IsActive(principal));
                Require(!principal.ToString().Contains(principal.SessionId, StringComparison.Ordinal));
                string request = Secret();
                DeviceSession rotated = repository.Refresh(first.RefreshToken, request);
                Require(repository.Authorize(rotated.AccessToken).SessionId == principal.SessionId);
                Require(repository.Refresh(first.RefreshToken, request) == rotated && repository.IsActive(principal));
                Unauthorized(() => repository.Authorize(first.AccessToken));
                clock.Now = clock.Now.AddSeconds(31); repository.Sweep();
                Require(repository.Authorize(rotated.AccessToken).SessionId == principal.SessionId);
                try { repository.Refresh(first.RefreshToken, Secret()); throw new InvalidOperationException("Reuse accepted"); }
                catch (AccountException error) when (error.Code == "sessionRevoked") { }
                Require(!repository.IsActive(principal) && repository.IsActive(sibling) && repository.IsActive(foreign));
                repository.Logout(second.RefreshToken);
                Require(!repository.IsActive(sibling) && repository.IsActive(foreign));
                surviving = repository.SignIn("restart", "Restart", "restart@example.invalid");
                oldProcess = repository.Authorize(surviving.AccessToken);
                byte[] plaintext = protection.CreateProtector("SyncWatch.Accounts", "v2")
                    .Unprotect(File.ReadAllBytes(Path.Combine(directory, "accounts.protected")));
                Require(!Encoding.UTF8.GetString(plaintext).Contains(oldProcess.SessionId, StringComparison.Ordinal));
            }
            using (var restored = new AccountRepository(directory, protection, clock))
            {
                AccountPrincipal current = restored.Authorize(surviving.AccessToken);
                Require(current.AccountId == oldProcess.AccountId && current.SessionId != oldProcess.SessionId);
                Require(!restored.IsActive(oldProcess) && restored.IsActive(current));
                clock.Now = clock.Now.AddMinutes(11);
                Require(!restored.IsActive(current));
                Unauthorized(() => restored.Authorize(surviving.AccessToken));
                DeviceSession renewed = restored.Refresh(surviving.RefreshToken, Secret());
                Require(restored.Authorize(renewed.AccessToken).SessionId == current.SessionId && restored.IsActive(current));
                clock.Now = clock.Now.AddDays(30);
                Require(!restored.IsActive(current));
                Unauthorized(() => restored.Refresh(renewed.RefreshToken, Secret()));
            }
            Console.WriteLine("Account authority checks passed: device separation, rotation/pruning continuity, reuse/logout/expiry and restart invalidation");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true); }
    }
    private static string Secret() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Account authority assertion failed"); }
    private static void Unauthorized(Action action)
    {
        try { action(); } catch (AccountException error) when (error.Status == 401) { return; }
        throw new InvalidOperationException("Unauthorized account authority accepted");
    }
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }
}
