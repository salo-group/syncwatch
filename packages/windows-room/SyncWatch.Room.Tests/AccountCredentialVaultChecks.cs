using SyncWatch.Room;

static class AccountCredentialVaultChecks
{
    public static void Run()
    {
        foreach (string scope in new[] { "", new string('a', 63), new string('A', 64), new string('a', 64) + "\n" })
        {
            bool rejected = false;
            try { _ = new AccountCredentialVault(scope); } catch (ArgumentException) { rejected = true; }
            if (!rejected) throw new Exception("Invalid storage scope accepted");
        }
        var invalid = new AccountCredentialVault(new string('a', 64));
        foreach (byte[] data in new[] { Array.Empty<byte>(), new byte[2049] })
        {
            bool rejected = false;
            try { invalid.Write(data); } catch (ArgumentException) { rejected = true; }
            if (!rejected) throw new Exception("Invalid storage record accepted");
        }
        if (Environment.GetEnvironmentVariable("SYNCWATCH_ACCOUNT_VAULT_CHECKS") != "1") return;
        string[] scopes = [Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"), Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N")];
        if (Environment.GetEnvironmentVariable("SYNCWATCH_ACCOUNT_VAULT_SCOPES") is string journal) File.WriteAllLines(journal, scopes);
        var vault = new AccountCredentialVault(scopes[0]);
        var independent = new AccountCredentialVault(scopes[1]);
        Exception? failure = null;
        try
        {
            if (vault.Read() is not null) throw new Exception("Owned storage scope was not empty");
            byte[] first = [0, 1, 2, 255], second = Enumerable.Repeat((byte)42, 2048).ToArray();
            vault.Write(first);
            if (!(vault.Read() ?? []).SequenceEqual(first) || independent.Read() is not null) throw new Exception("Storage isolation failed");
            vault.Write(second);
            if (!(vault.Read() ?? []).SequenceEqual(second)) throw new Exception("Storage replacement failed");
            vault.Remove();
            if (vault.Read() is not null) throw new Exception("Storage deletion failed");
            vault.Remove();
            Console.WriteLine("SYNCWATCH_ACCOUNT_VAULT_OS_OK");
        }
        catch (Exception error) { failure = error; throw; }
        finally
        {
            foreach (var owned in new[] { vault, independent })
            {
                try { owned.Remove(); }
                catch (Exception cleanup) { if (failure is null) throw; else throw new AggregateException(failure, cleanup); }
            }
        }
    }
}
