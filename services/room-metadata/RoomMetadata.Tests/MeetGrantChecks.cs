using System.Text;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;
using SyncWatch.RoomMetadata.Calls;
using SyncWatch.RoomMetadata.Rooms;

internal static class MeetGrantChecks
{
    internal static void Run()
    {
        if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Meet/account protection requires Windows validation");
        string root = Path.Combine(Path.GetTempPath(), "syncwatch-meet-grants-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        IDataProtectionProvider Protection() => DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(root, "keys")),
            builder =>
            {
                builder.SetApplicationName("SyncWatch.Meet.Tests");
                if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("Windows key protection is required");
                builder.ProtectKeysWithDpapi();
            });
        string original = "owned-synthetic-refresh-original-" + new string('A', 64);
        string replacement = "owned-synthetic-refresh-replacement-" + new string('B', 64);
        string file = Path.Combine(root, "meet", "grants.protected");
        try
        {
            using var accounts = new AccountRepository(root, Protection());
            var owner = accounts.SignIn("owned-meet-subject", "Owner", "owner@example.invalid");
            var other = accounts.SignIn("other-meet-subject", "Other", "owner@example.invalid");
            var principal = accounts.Authorize(owner.AccessToken); var otherPrincipal = accounts.Authorize(other.AccessToken);
            using (var grants = new MeetGrantRepository(accounts, root, Protection()))
            {
                Require(!grants.HasAuthorization(principal));
                Reject(() => grants.Remember(principal, "other-meet-subject", original), "providerIdentityInvalid");
                Reject(() => grants.Remember(principal, "owned-meet-subject", null), "providerIdentityInvalid");
                Require(!File.Exists(file));
                grants.Remember(principal, "owned-meet-subject", original);
                Require(grants.HasAuthorization(principal) && !grants.HasAuthorization(otherPrincipal));
                Require(File.ReadAllBytes(file).AsSpan().IndexOf(Encoding.UTF8.GetBytes(original)) < 0);
                Require(!grants.Forget(otherPrincipal) && grants.HasAuthorization(principal));
                Reject<IOException>(() => { using var duplicate = new MeetGrantRepository(accounts, root, Protection()); });
                // A failed replacement must retain the preceding acknowledged record and remove its temporary ciphertext.
                File.Move(file, file + ".backup"); Directory.CreateDirectory(file);
                try { RejectWrite(() => grants.Remember(principal, "owned-meet-subject", replacement)); }
                finally { Directory.Delete(file); File.Move(file + ".backup", file); }
                Require(grants.HasAuthorization(principal));
                Require(!Directory.EnumerateFiles(Path.GetDirectoryName(file)!, "*.tmp").Any());
            }
            using (var restored = new MeetGrantRepository(accounts, root, Protection()))
            {
                Require(restored.HasAuthorization(principal));
                restored.Remember(principal, "owned-meet-subject", null);
                accounts.Logout(owner.RefreshToken);
                Reject(() => restored.HasAuthorization(principal), "unauthorized");
                var signedInAgain = accounts.SignIn("owned-meet-subject", "Owner", "owner@example.invalid");
                principal = accounts.Authorize(signedInAgain.AccessToken);
                Require(restored.HasAuthorization(principal));
                Require(restored.Forget(principal) && !restored.Forget(principal) && !restored.HasAuthorization(principal));
                restored.Remember(principal, "owned-meet-subject", original);
            }
            byte[] valid = File.ReadAllBytes(file);
            Reject<System.Security.Cryptography.CryptographicException>(() => Protection().CreateProtector("SyncWatch.Accounts", "v2").Unprotect(valid));
            File.WriteAllBytes(file, [1, 2, 3]);
            Reject<System.Security.Cryptography.CryptographicException>(() => { using var invalid = new MeetGrantRepository(accounts, root, Protection()); });
            File.WriteAllBytes(file, valid);
            // Encryption purpose and format are independent of the device-session store.
            var protector = Protection().CreateProtector("SyncWatch.Meet", "grants", "v1");
            File.WriteAllBytes(file, protector.Protect(Encoding.UTF8.GetBytes("{\"version\":99,\"grants\":[]}")));
            Reject(() => { using var future = new MeetGrantRepository(accounts, root, Protection()); }, "accountStoreInvalid");
            File.WriteAllBytes(file, valid);
            using (var final = new MeetGrantRepository(accounts, root, Protection())) Require(final.HasAuthorization(principal));
            File.Move(file, file + ".backup"); Directory.CreateDirectory(file);
            try { Reject(() => { using var invalidPath = new MeetGrantRepository(accounts, root, Protection()); }, "accountStoreInvalid"); }
            finally { Directory.Delete(file); File.Move(file + ".backup", file); }

            var rooms = new AuthenticatedRooms(accounts, "127.0.0.1", 8999);
            var admission = rooms.Create(principal, "Owner", "meet-owner-run", "meet-create-request");
            RoomMemberAccess access = rooms.Access(principal, admission.RoomId, admission.MemberId, "meet-owner-run", owner: true);
            Require(access.Owner && access.OwnerAccountId == principal.AccountId && rooms.IsCurrent(access));
            Require(!rooms.IsCurrent(access with { OwnerAccountId = otherPrincipal.AccountId }));
            Require(!rooms.IsCurrent(access with { RoomEpoch = "obsolete-epoch" }));
            Require(!rooms.IsCurrent(access with { ClientRunId = "other-run" }));
            rooms.Leave(principal, admission.RoomId, admission.MemberId, "meet-owner-run");
            Require(!rooms.IsCurrent(access));
        }
        finally { Directory.Delete(root, recursive: true); }
        Console.WriteLine("SYNCWATCH_MEET_GRANTS_OK subject binding, encrypted DPAPI restart, atomic failure, scoped deletion, corruption and room epoch");
    }
    private static void Require(bool value) { if (!value) throw new InvalidOperationException("Meet grant invariant"); }
    private static void Reject(Action action, string code)
    {
        try { action(); throw new InvalidOperationException("Expected " + code); }
        catch (AccountException error) when (error.Code == code) { }
    }
    private static void RejectWrite(Action action)
    {
        try { action(); throw new InvalidOperationException("Expected storage replacement failure"); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }
    private static void Reject<T>(Action action) where T : Exception
    {
        try { action(); throw new InvalidOperationException("Expected " + typeof(T).Name); }
        catch (T) { }
    }
}
